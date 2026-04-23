"""
bot/vk_handler.py — Обработчик входящих сообщений от пользователей ВКонтакте

Как работает:
  1. Получает адрес Long Poll сервера через groups.getLongPollServer
  2. Бесконечно опрашивает сервер на новые события
  3. При событии message_new начинает диалог сбора заявки
  4. Задаёт 5 вопросов (имя, телефон, даты, состав, бюджет)
  5. Уведомляет руководителя в Telegram и сохраняет в Google Sheets

Требования:
  - В сообществе включены "Сообщения сообщества"
  - В настройках API включён "Bots Long Poll API"
  - Токен имеет права: messages, wall, photos

Запускается в отдельном потоке параллельно с Telegram и MAX ботами.
"""

import re
import time
import random
import logging
import threading
import requests
from typing import Optional


def _is_valid_phone(text: str) -> bool:
    """Телефон валиден если содержит не менее 10 цифр."""
    digits = re.sub(r"\D", "", text)
    return len(digits) >= 10

from config import Config
from sheets.client import SheetsClient

logger = logging.getLogger(__name__)

VK_API = "https://api.vk.com/method"
VK_VER = "5.199"

# Состояния диалога
STEP_START    = "start"
STEP_NAME     = "ask_name"
STEP_PHONE    = "ask_phone"
STEP_DATES    = "ask_dates"
STEP_TOURISTS = "ask_tourists"
STEP_BUDGET   = "ask_budget"
STEP_DONE     = "done"

# Хранилище состояний диалога: user_id → {step, name, phone, ...}
_conversations: dict = {}


def _vk_call(method: str, params: dict) -> dict:
    """Делает запрос к VK API."""
    try:
        resp = requests.get(
            f"{VK_API}/{method}",
            params={"access_token": Config.VK_TOKEN, "v": VK_VER, **params},
            timeout=30,
        )
        result = resp.json()
        if "error" in result:
            err = result["error"]
            logger.warning(f"VK API ошибка {err.get('error_code')}: {err.get('error_msg')}")
        return result
    except Exception as e:
        logger.warning(f"VK: ошибка запроса {method}: {e}")
        return {}


def _send(user_id: int, text: str) -> None:
    """Отправляет сообщение пользователю ВКонтакте."""
    _vk_call("messages.send", {
        "user_id":   user_id,
        "message":   text,
        "random_id": random.randint(1, 2**31),
    })


def _notify_admin(lead: dict) -> None:
    """Уведомляет всех админов о новой заявке через Telegram."""
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_ADMIN_IDS:
        return
    vk_link = f"vk.com/id{lead.get('user_id', '')}"
    text = (
        f"📥 <b>Новая заявка из ВКонтакте!</b>\n\n"
        f"👤 Имя: {lead.get('name', '—')}\n"
        f"📞 Телефон: {lead.get('phone', '—')}\n"
        f"📅 Даты: {lead.get('dates', '—')}\n"
        f"👨‍👩‍👧 Состав: {lead.get('tourists', '—')}\n"
        f"💰 Бюджет: {lead.get('budget', '—')}\n"
        f"🔗 ВК: {vk_link}"
    )
    for admin_id in Config.TELEGRAM_ADMIN_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": admin_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"VK: не удалось уведомить админа {admin_id}: {e}")


def _save_lead(lead: dict) -> None:
    """Сохраняет заявку в Google Sheets."""
    if not Config.GOOGLE_CREDENTIALS_FILE or not Config.GOOGLE_SHEET_ID:
        return
    try:
        sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
        sheets.add_lead({
            "name":     lead.get("name", "—"),
            "phone":    lead.get("phone", "—"),
            "tour":     "из ВКонтакте",
            "dates":    lead.get("dates", "—"),
            "tourists": lead.get("tourists", "—"),
            "budget":   lead.get("budget", "—"),
            "tg_id":    str(lead.get("user_id", "")),
            "tg_user":  f"vk.com/id{lead.get('user_id', '')}",
            "source":   "ВКонтакте",
        })
    except Exception as e:
        logger.warning(f"VK: не удалось сохранить заявку в Sheets: {e}")


def _handle_message(user_id: int, text: str) -> None:
    """Обрабатывает входящее сообщение и ведёт диалог."""
    text  = (text or "").strip()
    state = _conversations.get(user_id, {})
    step  = state.get("step", STEP_START)

    # Начало диалога
    if step == STEP_START or text.lower() in ("начать", "привет", "хочу тур"):
        _conversations[user_id] = {"step": STEP_NAME}
        _send(user_id,
            "👋 Здравствуйте! Я бот турагентства Пегас Туристик Уфа.\n\n"
            "Помогу оформить заявку на тур — отвечу на несколько вопросов.\n\n"
            "Как вас зовут? (Имя и фамилия)"
        )
        return

    # Шаг 1: имя
    if step == STEP_NAME:
        state["name"] = text
        state["step"] = STEP_PHONE
        _conversations[user_id] = state
        first = text.split()[0]
        _send(user_id,
            f"Приятно познакомиться, {first}! 😊\n\n"
            "Укажите ваш номер телефона — менеджер свяжется в течение 1 часа:"
        )
        return

    # Шаг 2: телефон
    if step == STEP_PHONE:
        if not _is_valid_phone(text):
            _send(user_id,
                "Пожалуйста, укажите ваш номер телефона цифрами (минимум 10 цифр).\n\n"
                "Например: +7 917 044-21-00"
            )
            return
        state["phone"] = text
        state["step"]  = STEP_DATES
        _conversations[user_id] = state
        _send(user_id,
            "Отлично! 📅\n\n"
            "Какие даты вылета вас интересуют?\n"
            "Напишите конкретные даты или один из вариантов:\n"
            "— Конкретные даты\n"
            "— Гибкие даты (любые ближайшие)\n"
            "— Могу лететь в любое время"
        )
        return

    # Шаг 3: даты
    if step == STEP_DATES:
        state["dates"] = text
        state["step"]  = STEP_TOURISTS
        _conversations[user_id] = state
        _send(user_id,
            "Понял! 👨‍👩‍👧\n\n"
            "Кто едет? Напишите состав группы:\n"
            "— 1 взрослый\n"
            "— 2 взрослых\n"
            "— 2 взрослых + 1 ребёнок\n"
            "— 2 взрослых + 2 детей\n"
            "— Другой состав"
        )
        return

    # Шаг 4: состав
    if step == STEP_TOURISTS:
        state["tourists"] = text
        state["step"]     = STEP_BUDGET
        _conversations[user_id] = state
        _send(user_id,
            "Почти готово! 💰\n\n"
            "Примерный бюджет на одного человека:\n"
            "— До 50 000 ₽/чел\n"
            "— 50 000 — 80 000 ₽/чел\n"
            "— 80 000 — 120 000 ₽/чел\n"
            "— Более 120 000 ₽/чел"
        )
        return

    # Шаг 5: бюджет — диалог завершён
    if step == STEP_BUDGET:
        state["budget"]  = text
        state["step"]    = STEP_DONE
        state["user_id"] = user_id
        _conversations[user_id] = state

        _save_lead(state)
        _notify_admin(state)

        first = state.get("name", "").split()[0] or "вас"
        _send(user_id,
            f"✅ Отлично, {first}!\n\n"
            "Ваша заявка принята. Менеджер свяжется с вами в течение 1 часа.\n\n"
            "Если хотите написать напрямую:\n"
            "📞 +7 (917) 044-21-00\n"
            "🏢 Уфа, проспект Октября 21/4\n\n"
            "До встречи на курорте! 🌴"
        )
        _conversations[user_id] = {"step": STEP_START}
        return

    # Неизвестное состояние — начинаем заново
    _conversations[user_id] = {"step": STEP_START}
    _handle_message(user_id, text)


def _get_long_poll_server() -> Optional[dict]:
    """Получает параметры Long Poll сервера."""
    result = _vk_call("groups.getLongPollServer", {"group_id": Config.VK_GROUP_ID})
    return result.get("response")


def _poll_once(server: str, key: str, ts: str) -> str:
    """
    Один цикл опроса Long Poll сервера.
    Возвращает новый ts для следующего запроса.
    """
    try:
        resp = requests.get(
            server,
            params={"act": "a_check", "key": key, "ts": ts, "wait": 25},
            timeout=30,
        )
        data = resp.json()
    except Exception as e:
        logger.warning(f"VK polling ошибка: {e}")
        return ts

    # Ошибки Long Poll — нужно переподключиться
    if "failed" in data:
        raise ConnectionError(f"VK Long Poll failed: {data['failed']}")

    new_ts = data.get("ts", ts)

    for update in data.get("updates", []):
        if update.get("type") != "message_new":
            continue

        message = update.get("object", {}).get("message", {})
        user_id = message.get("from_id")
        text    = message.get("text", "")

        # Пропускаем сообщения из бесед и групп (только личные)
        peer_id = message.get("peer_id", 0)
        if peer_id != user_id:
            continue

        if not user_id or user_id < 0:
            continue

        logger.info(f"VK: сообщение от user_id={user_id}: {text[:50]}")
        try:
            _handle_message(user_id, text)
        except Exception as e:
            logger.error(f"VK: ошибка обработки сообщения: {e}")

    return new_ts


def run_polling() -> None:
    """
    Запускает бесконечный цикл Long Poll.
    Вызывается в отдельном потоке из main.py.
    """
    if not Config.VK_TOKEN or not Config.VK_GROUP_ID:
        logger.info("VK: токен или group_id не заданы, polling не запущен")
        return

    logger.info("🚀 VK бот запущен (long polling)")

    while True:
        try:
            server_data = _get_long_poll_server()
            if not server_data:
                logger.warning("VK: не удалось получить Long Poll сервер, повтор через 30 сек")
                time.sleep(30)
                continue

            server = server_data["server"]
            key    = server_data["key"]
            ts     = server_data["ts"]

            logger.info(f"VK: подключён к Long Poll серверу")

            while True:
                ts = _poll_once(server, key, ts)

        except ConnectionError as e:
            logger.warning(f"VK: {e}, переподключение...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"VK polling критическая ошибка: {e}")
            time.sleep(10)


def start_in_background() -> threading.Thread:
    """Запускает polling в фоновом потоке. Вызывается из main.py."""
    thread = threading.Thread(target=run_polling, daemon=True, name="vk-polling")
    thread.start()
    logger.info("✅ VK polling поток запущен")
    return thread
