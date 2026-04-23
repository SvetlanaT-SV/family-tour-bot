"""
main.py — Точка входа. Запускает всю систему.

Что делает:
  1. Каждые 5 минут проверяет лист "Туры к публикации" в Google Sheets
  2. Каждые 4 часа ищет горящие туры через Tourvisor API автоматически
  3. Найденные туры присылает на одобрение (✅/❌)
  4. Параллельно держит Telegram-бота для сбора заявок от клиентов

Запуск:  python main.py
"""

import asyncio
import json
import logging
import os
import re
import requests as _requests
from io import BytesIO
from datetime import datetime, timedelta
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, ContextTypes

from config import Config
from ai.generator import generate_post, generate_post_without_ai, generate_post_from_dict
from tourvisor.client import TourvisorClient
from publisher.telegram import TelegramPublisher
from publisher.vk import VKPublisher
from publisher.max import MAXPublisher
from sheets.client import SheetsClient
from bot.handler import build_application
from bot.max_handler import start_in_background as start_max_polling
from bot.vk_handler import start_in_background as start_vk_polling

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Флаг режима: True = присылать на одобрение, False = автопилот ──
APPROVAL_MODE = True

# Файл для сохранения постов между перезапусками
_PENDING_FILE = os.path.join(os.path.dirname(__file__), "pending_posts.json")


def _load_pending() -> dict:
    """Загружает pending_posts из файла (если есть)."""
    if os.path.exists(_PENDING_FILE):
        try:
            with open(_PENDING_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data:
                    logger.info(f"📂 Загружено {len(data)} отложенных постов из файла")
                return data
        except Exception:
            pass
    return {}


def _save_pending(posts: dict) -> None:
    """Сохраняет pending_posts в файл."""
    try:
        with open(_PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Не удалось сохранить pending_posts: {e}")


# Хранилище постов ожидающих одобрения: tour_id → {text, photo_url}
PENDING_POSTS: dict = _load_pending()

# Файл для запланированных постов
_SCHEDULED_FILE = os.path.join(os.path.dirname(__file__), "scheduled_posts.json")


def _load_scheduled() -> list:
    """Загружает очередь запланированных постов из файла."""
    if os.path.exists(_SCHEDULED_FILE):
        try:
            with open(_SCHEDULED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or []
                if data:
                    logger.info(f"📂 Загружено {len(data)} запланированных постов")
                return data
        except Exception:
            pass
    return []


def _save_scheduled(posts: list) -> None:
    try:
        with open(_SCHEDULED_FILE, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Не удалось сохранить scheduled_posts: {e}")


SCHEDULED_POSTS: list = _load_scheduled()


async def check_scheduled_posts(context: ContextTypes.DEFAULT_TYPE = None):
    """Каждую минуту публикует запланированные посты, у которых пришло время."""
    if not SCHEDULED_POSTS:
        return

    from datetime import datetime, timezone
    import base64
    from bot.handler import publish_to_channels

    now = datetime.now(timezone.utc)
    due = [p for p in SCHEDULED_POSTS if datetime.fromisoformat(p["scheduled_for"]) <= now]
    if not due:
        return

    logger.info(f"⏰ Время публикации: {len(due)} пост(ов)")

    for entry in due:
        try:
            photo_bytes = base64.b64decode(entry["photo_b64"]) if entry.get("photo_b64") else None
            ok, _ = await publish_to_channels(
                context.bot,
                entry.get("text", ""),
                photo_url=entry.get("photo_url") or None,
                photo_bytes=photo_bytes,
                tour_id=entry.get("tour_id", ""),
                overlay_country=entry.get("overlay_country", ""),
                overlay_price=entry.get("overlay_price", ""),
                overlay_departure=entry.get("overlay_departure", ""),
            )
            logger.info(f"Запланированный пост {entry.get('tour_id')}: ok={ok}")
        except Exception as e:
            logger.error(f"Ошибка публикации запланированного поста: {e}")
        finally:
            SCHEDULED_POSTS.remove(entry)
            _save_scheduled(SCHEDULED_POSTS)

# ── Tourvisor: отслеживание уже отправленных туров ─────────────
_SENT_TOURS_FILE = os.path.join(os.path.dirname(__file__), "sent_tours.json")


def _load_sent_tours() -> dict:
    """Загружает словарь tour_id → ISO-дата отправки."""
    if os.path.exists(_SENT_TOURS_FILE):
        try:
            with open(_SENT_TOURS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_sent_tours(sent: dict) -> None:
    try:
        # Оставляем только туры за последние 14 дней
        cutoff = (datetime.now() - timedelta(days=14)).isoformat()
        sent = {k: v for k, v in sent.items() if v >= cutoff}
        with open(_SENT_TOURS_FILE, "w", encoding="utf-8") as f:
            json.dump(sent, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Не удалось сохранить sent_tours: {e}")


def _fetch_tourvisor_tours():
    """Синхронный поиск туров через Tourvisor API (запускается в executor)."""
    login = Config.TOURVISOR_LOGIN
    password = Config.TOURVISOR_PASSWORD
    logger.info(f"Tourvisor: login={login!r} (len={len(login)}), password len={len(password)}")
    client = TourvisorClient(login, password)
    departure_id = client.find_city_id("Уфа")
    if not departure_id:
        logger.error("Tourvisor: город Уфа не найден в справочнике")
        return []
    logger.info(f"Tourvisor: поиск из Уфы (ID={departure_id}), до {Config.MAX_PRICE}₽, {Config.NIGHTS_FROM}-{Config.NIGHTS_TO} ночей")
    return client.find_hot_tours(
        departure_id=departure_id,
        nights_from=Config.NIGHTS_FROM,
        nights_to=Config.NIGHTS_TO,
        days_ahead=Config.DAYS_AHEAD,
        price_max=Config.MAX_PRICE,
    )


async def search_tourvisor_tours(context: ContextTypes.DEFAULT_TYPE = None):
    """
    Каждые 4 часа ищет горящие туры в Tourvisor и присылает на одобрение.
    Отправляет не более 3 новых туров за один запуск.
    """
    if not Config.TOURVISOR_LOGIN or not Config.TOURVISOR_PASSWORD:
        return

    logger.info("🔍 Tourvisor: запускаю поиск горящих туров...")

    loop = asyncio.get_event_loop()
    try:
        tours = await loop.run_in_executor(None, _fetch_tourvisor_tours)
    except Exception as e:
        logger.error(f"Tourvisor: ошибка поиска: {e}")
        return

    if not tours:
        logger.info("Tourvisor: туры не найдены")
        return

    sent = _load_sent_tours()
    new_tours = [t for t in tours if t.tour_id and t.tour_id not in sent]
    logger.info(f"Tourvisor: всего {len(tours)}, новых {len(new_tours)}")

    bot = context.bot
    count = 0
    for tour in new_tours:
        if count >= 3:
            break
        try:
            if Config.ANTHROPIC_API_KEY:
                text = generate_post(tour, Config.ANTHROPIC_API_KEY)
            else:
                text = generate_post_without_ai(tour)
            photo_url = tour.photo_url or None
            tour_id = f"tv_{tour.tour_id}"

            PENDING_POSTS[tour_id] = {
                "text":            text,
                "photo_url":       photo_url,
                "overlay_country": tour.country,
                "overlay_price":   tour.formatted_price_per_person,
                "overlay_departure": f"{tour.date_from} · из {tour.city_from}" if tour.date_from else "",
            }
            _save_pending(PENDING_POSTS)

            preview = f"📋 <b>НОВЫЙ ГОРЯЩИЙ ТУР — на одобрение:</b>\n\n{text}"
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Сейчас",       callback_data=f"approve_{tour_id}"),
                    InlineKeyboardButton("⏰ По расписанию", callback_data=f"schedule_{tour_id}"),
                ],
                [InlineKeyboardButton("❌ Пропустить", callback_data=f"reject_{tour_id}")],
            ])

            photo_content = None
            if photo_url:
                try:
                    resp = _requests.get(photo_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200:
                        photo_content = resp.content
                except Exception:
                    pass

            for admin_id in Config.TELEGRAM_ADMIN_IDS:
                try:
                    if photo_content:
                        await bot.send_photo(
                            chat_id=admin_id,
                            photo=BytesIO(photo_content),
                            caption=preview,
                            parse_mode="HTML",
                            reply_markup=keyboard,
                        )
                    else:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=preview,
                            parse_mode="HTML",
                            reply_markup=keyboard,
                        )
                except Exception as send_err:
                    logger.warning(f"Не удалось отправить админу {admin_id}: {send_err}")

            sent[tour.tour_id] = datetime.now().isoformat()
            count += 1
            logger.info(f"Tourvisor: отправлен на одобрение — {tour.hotel_name}, {tour.country}")

        except Exception as e:
            logger.error(f"Tourvisor: ошибка при отправке тура {tour.hotel_name}: {e}")

    _save_sent_tours(sent)


async def publish_from_sheets(context: ContextTypes.DEFAULT_TYPE = None):
    """
    Проверяет Google Sheets каждые 5 минут.
    Берёт туры со статусом НОВЫЙ и публикует их.
    """
    if not Config.GOOGLE_CREDENTIALS_FILE or not Config.GOOGLE_SHEET_ID:
        return

    sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
    pending = sheets.get_pending_tours()

    if not pending:
        return

    logger.info(f"📋 Sheets: найдено {len(pending)} тура(ов) к публикации")

    for tour_row in pending:
        row_num = tour_row["_row_number"]
        name = f"{tour_row.get('Отель', '?')} / {tour_row.get('Страна', '?')}"

        sheets.mark_tour_publishing(row_num)

        try:
            logger.info(f"  Генерирую пост: {name}")
            logger.info(f"  Данные тура: { {k: v for k, v in tour_row.items() if not k.startswith('_')} }")
            text = generate_post_from_dict(tour_row, Config.ANTHROPIC_API_KEY)
            photo_url = str(tour_row.get("Фото URL", "") or "").strip() or None

            # Данные для наложения текста на фото
            ov_country = str(tour_row.get("Страна", "") or "").strip()
            ov_price_raw = str(tour_row.get("Цена/чел", "") or "").strip()
            price_digits = re.sub(r"[^\d]", "", ov_price_raw)
            ov_price = f"{int(price_digits):,} ₽/чел".replace(",", " ") if price_digits else ""
            date_str = str(tour_row.get("Дата вылета", "") or "").strip()
            city_str = str(tour_row.get("Город вылета", "") or "").strip() or "Уфа"
            ov_departure = f"{date_str} · из {city_str}" if date_str else ""

            bot = context.bot  # используем уже авторизованного бота

            if APPROVAL_MODE:
                tour_id = f"sheets_{row_num}"
                PENDING_POSTS[tour_id] = {
                    "text":            text,
                    "photo_url":       photo_url,
                    "overlay_country": ov_country,
                    "overlay_price":   ov_price,
                    "overlay_departure": ov_departure,
                }
                _save_pending(PENDING_POSTS)
                preview = f"📋 <b>НОВЫЙ ГОРЯЩИЙ ТУР — на одобрение:</b>\n\n{text}"
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Сейчас",       callback_data=f"approve_{tour_id}"),
                        InlineKeyboardButton("⏰ По расписанию", callback_data=f"schedule_{tour_id}"),
                    ],
                    [InlineKeyboardButton("❌ Пропустить", callback_data=f"reject_{tour_id}")],
                ])

                # Скачиваем фото один раз, отправляем всем админам
                photo_content = None
                if photo_url:
                    try:
                        resp = _requests.get(photo_url, timeout=10, headers={
                            "User-Agent": "Mozilla/5.0"
                        })
                        if resp.status_code == 200:
                            photo_content = resp.content
                    except Exception as photo_err:
                        logger.warning(f"  Фото не загрузилось: {photo_err}")

                for admin_id in Config.TELEGRAM_ADMIN_IDS:
                    try:
                        if photo_content:
                            await bot.send_photo(
                                chat_id=admin_id,
                                photo=BytesIO(photo_content),
                                caption=preview,
                                parse_mode="HTML",
                                reply_markup=keyboard,
                            )
                        else:
                            await bot.send_message(
                                chat_id=admin_id,
                                text=preview,
                                parse_mode="HTML",
                                reply_markup=keyboard,
                            )
                    except Exception as send_err:
                        logger.warning(f"  Не удалось отправить админу {admin_id}: {send_err}")

                sheets.mark_tour_status(row_num, "НА ОДОБРЕНИИ",
                                         published_at=datetime.now().strftime("%d.%m.%Y %H:%M"))
                logger.info(f"  📨 Отправлено на одобрение: {name}")
            else:
                tg = TelegramPublisher(
                    token=Config.TELEGRAM_BOT_TOKEN,
                    channel_id=Config.TELEGRAM_CHANNEL_ID,
                    admin_id=Config.TELEGRAM_ADMIN_IDS,
                )
                if Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHANNEL_ID:
                    tg.publish(text, photo_url)

                if Config.VK_TOKEN and Config.VK_GROUP_ID:
                    vk = VKPublisher(token=Config.VK_TOKEN, group_id=Config.VK_GROUP_ID)
                    vk.publish(text, photo_url)

                if Config.MAX_TOKEN and Config.MAX_CHAT_ID:
                    max_pub = MAXPublisher(token=Config.MAX_TOKEN, chat_id=Config.MAX_CHAT_ID)
                    max_pub.publish(text, photo_url)

                sheets.mark_tour_status(
                    row_num, "ОПУБЛИКОВАН",
                    published_at=datetime.now().strftime("%d.%m.%Y %H:%M")
                )
                for admin_id in Config.TELEGRAM_ADMIN_IDS:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=f"✅ Опубликован тур:\n{name}\n{text[:200]}...",
                        parse_mode="HTML",
                    )

        except Exception as e:
            logger.error(f"  ❌ Ошибка публикации {name}: {e}")
            sheets.mark_tour_status(row_num, "ОШИБКА", error=str(e))


async def post_init(application: Application) -> None:
    """Запускается после инициализации бота — стартуем планировщик"""
    logger.info("🚀 Family Tour Bot запускается...")
    logger.info(f"   Режим: {'одобрение' if APPROVAL_MODE else 'автопилот'}")

    # Проверяем Sheets каждые 5 минут через встроенный JobQueue
    application.job_queue.run_repeating(
        publish_from_sheets,
        interval=300,
        first=10,
        name="sheets_check",
    )
    logger.info("✅ Планировщик: проверка Sheets каждые 5 минут")

    # Поиск горящих туров в Tourvisor каждые 4 часа
    application.job_queue.run_repeating(
        search_tourvisor_tours,
        interval=4 * 3600,
        first=30,   # первый запуск через 30 сек после старта
        name="tourvisor_search",
    )
    logger.info("✅ Планировщик: поиск туров Tourvisor каждые 4 часа")

    # Публикация запланированных постов — каждую минуту
    application.job_queue.run_repeating(
        check_scheduled_posts,
        interval=60,
        first=30,
        name="scheduled_publish",
    )
    logger.info(f"✅ Планировщик: проверка расписания каждую минуту (слоты {Config.PUBLISH_HOURS} МСК)")


if __name__ == "__main__":
    import time
    from telegram.error import Conflict

    # Запускаем MAX polling в фоновом потоке
    # VK polling отключён — используется Senler
    start_max_polling()

    app = build_application(
        PENDING_POSTS,
        save_pending=_save_pending,
        scheduled_posts=SCHEDULED_POSTS,
        save_scheduled=_save_scheduled,
    )
    app.post_init = post_init

    # Если Telegram ещё держит старое соединение — ждём и повторяем
    for attempt in range(10):
        try:
            logger.info("✅ Telegram-бот запускается...")
            app.run_polling(drop_pending_updates=True)
            break
        except Conflict:
            wait = 15 * (attempt + 1)
            logger.warning(f"⚠️ Конфликт соединений — жду {wait} сек и повторяю...")
            time.sleep(wait)
        except Exception as e:
            logger.error(f"❌ Ошибка запуска: {e}")
            break
