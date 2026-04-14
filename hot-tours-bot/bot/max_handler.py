"""
bot/max_handler.py — Обработчик входящих сообщений от пользователей MAX

Как работает:
  1. Раз в 2 секунды опрашивает GET /updates (long polling)
  2. Когда пользователь пишет боту — начинает диалог сбора заявки
  3. Задаёт те же 5 вопросов что и Telegram бот (имя, телефон, даты, состав, бюджет)
  4. Уведомляет руководителя в Telegram о новой заявке
  5. Сохраняет заявку в Google Sheets

Запускается в отдельном потоке параллельно с Telegram ботом.
"""

import time
import logging
import threading
import requests
from typing import Optional

from config import Config
from sheets.client import SheetsClient

logger = logging.getLogger(__name__)

API_URL = "https://platform-api.max.ru"

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


def _headers() -> dict:
    return {"Authorization": Config.MAX_TOKEN}


def _send(user_id: int, text: str) -> None:
    """Отправляет сообщение пользователю MAX."""
    try:
        requests.post(
            f"{API_URL}/messages",
            headers=_headers(),
            params={"user_id": user_id},
            json={"text": text, "format": "markdown"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"MAX: не удалось отправить сообщение user_id={user_id}: {e}")


def _notify_admin(lead: dict) -> None:
    """Уведомляет руководителя о новой заявке через Telegram."""
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_ADMIN_ID:
        return
    text = (
        f"📥 <b>Новая заявка из MAX!</b>\n\n"
        f"👤 Имя: {lead.get('name', '—')}\n"
        f"📞 Телефон: {lead.get('phone', '—')}\n"
        f"📅 Даты: {lead.get('dates', '—')}\n"
        f"👨‍👩‍👧 Состав: {lead.get('tourists', '—')}\n"
        f"💰 Бюджет: {lead.get('budget', '—')}\n"
        f"🔗 MAX user_id: {lead.get('user_id', '—')}"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": Config.TELEGRAM_ADMIN_ID,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"MAX: не удалось уведомить администратора: {e}")


def _save_lead(lead: dict) -> None:
    """Сохраняет заявку в Google Sheets."""
    if not Config.GOOGLE_CREDENTIALS_FILE or not Config.GOOGLE_SHEET_ID:
        return
    try:
        sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
        sheets.add_lead({
            "name":     lead.get("name", "—"),
            "phone":    lead.get("phone", "—"),
            "tour":     "из MAX",
            "dates":    lead.get("dates", "—"),
            "tourists": lead.get("tourists", "—"),
            "budget":   lead.get("budget", "—"),
            "tg_id":    str(lead.get("user_id", "")),
            "tg_user":  f"MAX:{lead.get('user_id', '')}",
            "source":   "MAX бот",
        })
    except Exception as e:
        logger.warning(f"MAX: не удалось сохранить заявку в Sheets: {e}")


def _handle_message(user_id: int, text: str) -> None:
    """Обрабатывает входящее сообщение и ведёт диалог."""
    text = (text or "").strip()
    state = _conversations.get(user_id, {})
    step  = state.get("step", STEP_START)

    # /start или начало диалога
    if step == STEP_START or text.lower() in ("/start", "начать", "привет"):
        _conversations[user_id] = {"step": STEP_NAME}
        _send(user_id,
            "👋 Здравствуйте! Я бот турагентства **Пегас Туристик Уфа**.\n\n"
            "Помогу оформить заявку на тур — отвечу на несколько вопросов.\n\n"
            "Как вас зовут? (Имя и фамилия)"
        )
        return

    # Шаг 1: получили имя
    if step == STEP_NAME:
        state["name"] = text
        state["step"] = STEP_DATES
        _conversations[user_id] = state
        first = text.split()[0]
        _send(user_id,
            f"Приятно познакомиться, {first}! 😊\n\n"
            "Какие даты вылета вас интересуют?\n"
            "Напишите конкретные даты или выберите:\n"
            "— Конкретные даты\n"
            "— Гибкие даты (любые ближайшие)\n"
            "— Могу лететь в любое время"
        )
        return

    # Шаг 2: получили даты
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

    # Шаг 3: получили состав
    if step == STEP_TOURISTS:
        state["tourists"] = text
        state["step"]     = STEP_BUDGET
        _conversations[user_id] = state
        _send(user_id,
            "Отлично! 💰\n\n"
            "Примерный бюджет на одного человека:\n"
            "— До 50 000 ₽/чел\n"
            "— 50 000 — 80 000 ₽/чел\n"
            "— 80 000 — 120 000 ₽/чел\n"
            "— Более 120 000 ₽/чел"
        )
        return

    # Шаг 4: получили бюджет
    if step == STEP_BUDGET:
        state["budget"] = text
        state["step"]   = STEP_PHONE
        _conversations[user_id] = state
        _send(user_id,
            "Почти готово! 📞\n\n"
            "Укажите ваш номер телефона — менеджер свяжется в течение 1 часа:"
        )
        return

    # Шаг 5: получили телефон — диалог завершён
    if step == STEP_PHONE:
        state["phone"]   = text
        state["step"]    = STEP_DONE
        state["user_id"] = user_id
        _conversations[user_id] = state

        _save_lead(state)
        _notify_admin(state)

        first = state.get("name", "").split()[0] or "вас"
        _send(user_id,
            f"✅ Отлично, {first}!\n\n"
            "Ваша заявка принята. Менеджер свяжется с вами в течение **1 часа**.\n\n"
            "Если хотите написать напрямую:\n"
            "📞 +7 (917) 044-21-00\n"
            "🏢 Уфа, проспект Октября 21/4\n\n"
            "До встречи на курорте! 🌴"
        )

        # Сбрасываем состояние — следующее сообщение начнёт новый диалог
        _conversations[user_id] = {"step": STEP_START}
        return

    # Неизвестное состояние — начинаем заново
    _conversations[user_id] = {"step": STEP_START}
    _handle_message(user_id, text)


def _poll_once(marker: Optional[int]) -> Optional[int]:
    """
    Один цикл опроса обновлений.
    Возвращает новый marker для следующего запроса.
    """
    params = {"limit": 100, "timeout": 20, "types": "message_created,bot_started"}
    if marker:
        params["marker"] = marker

    try:
        resp = requests.get(
            f"{API_URL}/updates",
            headers=_headers(),
            params=params,
            timeout=30,
        )
        data = resp.json()
    except Exception as e:
        logger.warning(f"MAX polling ошибка: {e}")
        return marker

    new_marker = data.get("marker", marker)

    for update in data.get("updates", []):
        update_type = update.get("update_type")

        if update_type in ("message_created", "bot_started"):
            message = update.get("message", {})
            sender  = message.get("sender", {})
            body    = message.get("body", {})
            user_id = sender.get("user_id")
            text    = body.get("text", "")

            # Пропускаем сообщения от самого бота (нет sender у исходящих)
            if not user_id:
                continue

            # Пропускаем сообщения из канала (там нет диалога)
            recipient = message.get("recipient", {})
            if recipient.get("chat_type") == "channel":
                continue

            logger.info(f"MAX: сообщение от user_id={user_id}: {text[:50]}")
            try:
                _handle_message(user_id, text)
            except Exception as e:
                logger.error(f"MAX: ошибка обработки сообщения: {e}")

    return new_marker


def run_polling() -> None:
    """
    Запускает бесконечный цикл long polling.
    Вызывается в отдельном потоке из main.py.
    """
    if not Config.MAX_TOKEN:
        logger.info("MAX: токен не задан, polling не запущен")
        return

    logger.info("🚀 MAX бот запущен (long polling)")
    marker = None

    while True:
        try:
            marker = _poll_once(marker)
        except Exception as e:
            logger.error(f"MAX polling критическая ошибка: {e}")
            time.sleep(5)


def start_in_background() -> threading.Thread:
    """Запускает polling в фоновом потоке. Вызывается из main.py."""
    thread = threading.Thread(target=run_polling, daemon=True, name="max-polling")
    thread.start()
    logger.info("✅ MAX polling поток запущен")
    return thread
