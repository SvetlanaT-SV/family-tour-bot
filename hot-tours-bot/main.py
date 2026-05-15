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

# ── Журнал ошибок в Google Sheets ──
# Все WARNING/ERROR/CRITICAL автоматически пишутся в лист "Журнал ошибок"
# для быстрой диагностики без необходимости лезть в Railway logs.
from error_logger import install_error_logger, flush_buffer as flush_errors_buffer


def _notify_admin_on_critical(entry: dict) -> None:
    """Push в Telegram при CRITICAL ошибках."""
    if not (Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_ADMIN_IDS):
        return
    text = (
        f"🚨 <b>КРИТИЧЕСКАЯ ОШИБКА</b>\n\n"
        f"⏰ {entry.get('time', '')}\n"
        f"📍 {entry.get('logger', '')}\n"
        f"💬 {entry.get('message', '')[:500]}"
    )
    for admin_id in Config.TELEGRAM_ADMIN_IDS:
        try:
            _requests.post(
                f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": admin_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception:
            pass


install_error_logger(level=logging.WARNING, notify_admin=_notify_admin_on_critical)

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
    """
    Каждую минуту публикует запланированные посты у которых пришло время.
    Источник истины — лист 'Расписание' в Google Sheets (переживает перезапуски).
    Локальный SCHEDULED_POSTS — дублирующий кэш на случай недоступности Sheets.
    """
    from datetime import datetime, timezone
    import base64
    from bot.handler import publish_to_channels

    now = datetime.now(timezone.utc)
    sheets = None
    pending_in_sheets = []

    # Читаем из Google Sheets
    if Config.GOOGLE_CREDENTIALS_FILE and Config.GOOGLE_SHEET_ID:
        try:
            sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
            pending_in_sheets = sheets.get_pending_scheduled()
        except Exception as e:
            logger.warning(f"Расписание: не смог прочитать из Sheets: {e}")

    # Сводим: из Sheets (приоритет) + локальная очередь
    due_entries = []
    seen_tour_ids = set()

    for r in pending_in_sheets:
        try:
            slot_dt = datetime.fromisoformat(r.get("Когда", ""))
            if slot_dt <= now:
                due_entries.append({
                    "source":             "sheets",
                    "row_number":         r.get("_row_number"),
                    "tour_id":            r.get("tour_id", ""),
                    "text":               r.get("Текст", ""),
                    "photo_url":          r.get("Photo URL", ""),
                    "photo_b64":          r.get("Photo bytes", ""),
                    "overlay_country":    r.get("Overlay страна", ""),
                    "overlay_price":      r.get("Overlay цена", ""),
                    "overlay_departure":  r.get("Overlay вылет", ""),
                })
                seen_tour_ids.add(r.get("tour_id", ""))
        except Exception:
            continue

    for p in SCHEDULED_POSTS:
        try:
            slot_dt = datetime.fromisoformat(p["scheduled_for"])
            if slot_dt <= now and p.get("tour_id") not in seen_tour_ids:
                due_entries.append({
                    "source":             "local",
                    "tour_id":            p.get("tour_id", ""),
                    "text":               p.get("text", ""),
                    "photo_url":          p.get("photo_url", ""),
                    "photo_b64":          p.get("photo_b64", ""),
                    "overlay_country":    p.get("overlay_country", ""),
                    "overlay_price":      p.get("overlay_price", ""),
                    "overlay_departure":  p.get("overlay_departure", ""),
                    "_local_entry":       p,
                })
        except Exception:
            continue

    if not due_entries:
        return

    logger.info(f"⏰ Время публикации: {len(due_entries)} пост(ов)")

    for entry in due_entries:
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
            logger.info(f"Запланированный пост {entry.get('tour_id')}: ok={ok}, source={entry['source']}")

            # Помечаем в Sheets как опубликованный
            if entry["source"] == "sheets" and sheets and entry.get("row_number"):
                try:
                    sheets.mark_scheduled_status(entry["row_number"], "ОПУБЛИКОВАН")
                except Exception as se:
                    logger.warning(f"Расписание: не смог пометить как опубликованный: {se}")
            # Удаляем из локальной очереди
            if entry.get("_local_entry") and entry["_local_entry"] in SCHEDULED_POSTS:
                SCHEDULED_POSTS.remove(entry["_local_entry"])
                _save_scheduled(SCHEDULED_POSTS)
        except Exception as e:
            logger.error(f"Ошибка публикации запланированного поста: {e}")

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

            if photo_content and tour.country:
                try:
                    from image_overlay import add_tour_overlay
                    photo_content = add_tour_overlay(
                        photo_content,
                        country=tour.country,
                        price=tour.formatted_price_per_person,
                        departure=f"{tour.date_from} · из {tour.city_from}" if tour.date_from else "",
                    )
                except Exception as oe:
                    logger.warning(f"Overlay для превью не применён: {oe}")

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

                # Скачиваем фото один раз, накладываем текст, отправляем всем админам
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

                # Накладываем текст на превью — чтобы админ видел финальный вид
                if photo_content and ov_country:
                    try:
                        from image_overlay import add_tour_overlay
                        photo_content = add_tour_overlay(
                            photo_content,
                            country=ov_country,
                            price=ov_price,
                            departure=ov_departure,
                        )
                    except Exception as oe:
                        logger.warning(f"  Overlay для превью не применён: {oe}")

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
                    vk = VKPublisher(
                        token=Config.VK_TOKEN,
                        group_id=Config.VK_GROUP_ID,
                        user_token=Config.VK_USER_TOKEN or None,
                    )
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


async def flush_error_logs_job(context: ContextTypes.DEFAULT_TYPE = None):
    """Каждую минуту сбрасывает буфер ошибок в Google Sheets лист 'Журнал ошибок'."""
    if not (Config.GOOGLE_CREDENTIALS_FILE and Config.GOOGLE_SHEET_ID):
        return
    try:
        sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
        flushed = flush_errors_buffer(sheets)
        if flushed:
            # Не используем logger.info — это вызовет рекурсию (handler пишет в этот же буфер)
            print(f"📝 Журнал ошибок: выгружено {flushed} записей в Sheets")
    except Exception as e:
        print(f"⚠️ flush_error_logs_job: {e}")


async def collect_news_job(context: ContextTypes.DEFAULT_TYPE = None):
    """
    Раз в сутки собирает новости из туристических Telegram-каналов
    (список в Sheets лист 'Источники новостей'), GigaChat выбирает
    топ-3 и переписывает в наш стиль, отправляет на одобрение.
    """
    if not Config.GOOGLE_CREDENTIALS_FILE or not Config.GOOGLE_SHEET_ID:
        return
    if not os.getenv("GIGACHAT_AUTH_KEY", "").strip():
        logger.info("Новости: GIGACHAT_AUTH_KEY не задан, пропускаю")
        return

    from datetime import datetime, timezone, timedelta
    from news import fetch_channel_posts, select_and_rewrite

    sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
    channels = sheets.get_news_sources()
    if not channels:
        logger.info("Новости: нет активных каналов-источников")
        return

    logger.info(f"Новости: проверяю {len(channels)} канал(ов)")

    # Окно времени — последние 24 часа
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    all_posts = []
    for ch in channels:
        try:
            all_posts.extend(fetch_channel_posts(ch, since_dt=since))
        except Exception as e:
            logger.warning(f"Новости: канал {ch}: {e}")

    async def _notify_admins(text: str):
        """Шлёт служебное сообщение всем админам — для feedback по /news."""
        if not context or not getattr(context, "bot", None):
            return
        for admin_id in Config.TELEGRAM_ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Не смог уведомить админа {admin_id}: {e}")

    if not all_posts:
        logger.info("Новости: за сутки ничего не нашёл")
        sheets.set_meta("news_last_run_msk", datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d"))
        await _notify_admins("ℹ️ Новости: за последние сутки в каналах-источниках ничего не нашлось.")
        return

    logger.info(f"Новости: всего собрано {len(all_posts)} постов, отправляю в GigaChat")
    rewrites = select_and_rewrite(all_posts, top_n=3)
    if not rewrites:
        logger.warning("Новости: GigaChat не вернул переписанных постов")
        await _notify_admins(
            f"⚠️ Новости: собрал {len(all_posts)} постов из источников, но GigaChat не вернул "
            f"переписанные. Скорее всего сработал фильтр Сбера. Проверь логи: <code>docker logs bot --tail 100</code>"
        )
        return

    # Помечаем что сегодня сбор уже прошёл — чтобы при перезапуске не дублировать
    sheets.set_meta("news_last_run_msk", datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d"))

    bot = context.bot
    for i, item in enumerate(rewrites, 1):
        text = item.get("text", "").strip()
        if not text:
            continue

        tour_id = f"news_{int(datetime.now().timestamp())}_{i}"
        source = item.get("source_post") or {}
        photo_url = source.get("photo_url", "") or None
        src_url    = source.get("post_url", "")
        src_channel = source.get("channel_username", "")

        PENDING_POSTS[tour_id] = {
            "text":               text,
            "photo_url":          photo_url,
            "overlay_country":    "",     # для новостей оверлей не накладываем
            "overlay_price":      "",
            "overlay_departure":  "",
        }
        _save_pending(PENDING_POSTS)

        # В превью — служебная шапка с источником, в самом тексте поста её нет
        source_line = ""
        if src_url:
            source_line = f"\n🔗 Источник: <a href=\"{src_url}\">@{src_channel}</a>\n"
        preview = f"📰 <b>НОВОСТЬ — на одобрение:</b>{source_line}\n{text}"
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

        # Если у исходного поста не было картинки — генерируем заглушку,
        # чтобы пост в канале не выглядел голым текстом.
        if not photo_content:
            try:
                from image_overlay.news_placeholder import make_news_placeholder
                photo_content = make_news_placeholder()
                logger.info(f"Новости: использую placeholder для поста без фото ({len(photo_content)} байт)")
            except Exception as e:
                logger.warning(f"Новости: не смог сгенерировать placeholder: {e}")

        for admin_id in Config.TELEGRAM_ADMIN_IDS:
            try:
                if photo_content:
                    await bot.send_photo(
                        chat_id=admin_id, photo=BytesIO(photo_content),
                        caption=preview, parse_mode="HTML", reply_markup=keyboard,
                    )
                else:
                    await bot.send_message(
                        chat_id=admin_id, text=preview,
                        parse_mode="HTML", reply_markup=keyboard,
                    )
            except Exception as e:
                logger.warning(f"Новости: не смог отправить админу {admin_id}: {e}")

        logger.info(f"Новости: отправлен на одобрение tour_id={tour_id}")


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

    # Сброс журнала ошибок в Google Sheets — каждые 60 секунд
    application.job_queue.run_repeating(
        flush_error_logs_job,
        interval=60,
        first=45,
        name="flush_error_logs",
    )
    logger.info("✅ Планировщик: журнал ошибок выгружается в Sheets каждую минуту")

    # Сбор новостей раз в сутки в 8:00 МСК (= 5:00 UTC)
    from datetime import datetime, timezone, timedelta, time as dtime
    msk = timezone(timedelta(hours=3))
    application.job_queue.run_daily(
        collect_news_job,
        time=dtime(hour=8, minute=0, tzinfo=msk),
        name="news_collect",
    )
    logger.info("✅ Планировщик: сбор новостей в 8:00 МСК ежедневно")

    # Догон: если бот стартовал ПОСЛЕ 8:00 МСК и сегодняшний сбор ещё не прошёл —
    # запускаем разово через минуту. Защищает от пропусков из-за передеплоев.
    try:
        if Config.GOOGLE_CREDENTIALS_FILE and Config.GOOGLE_SHEET_ID:
            sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
            now_msk = datetime.now(msk)
            today_str = now_msk.strftime("%Y-%m-%d")
            last_run = sheets.get_meta("news_last_run_msk")
            if now_msk.hour >= 8 and last_run != today_str:
                logger.info(
                    f"Новости: догон — сегодня {today_str} ещё не было сбора "
                    f"(last_run={last_run!r}), запускаю через 60 сек"
                )
                application.job_queue.run_once(collect_news_job, when=60, name="news_catchup")
            else:
                logger.info(f"Новости: догон не нужен (now={now_msk.hour}h, last_run={last_run!r})")
    except Exception as e:
        logger.warning(f"Новости: проверка догона не удалась: {e}")


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
