"""
bot/handler.py — Telegram-бот для сбора заявок от клиентов

Когда клиент нажимает "Узнать подробнее" в посте — попадает в этот бот.
Бот задаёт 5 вопросов, собирает данные и:
  1. Сохраняет в Google Sheets
  2. Уведомляет руководителя
  3. Подтверждает клиенту что позвонят

Библиотека: python-telegram-bot
Документация: https://python-telegram-bot.readthedocs.io/
"""

import logging
import re
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from publisher.telegram import TelegramPublisher
from publisher.vk import VKPublisher
from publisher.max import MAXPublisher
from sheets.client import SheetsClient
from config import Config

# Включаем логирование — помогает видеть что происходит
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Состояния диалога (шаги разговора с клиентом) ─────────────
# ConversationHandler работает как конечный автомат:
# каждое состояние = один вопрос
ASK_NAME     = 0   # Шаг 1: спрашиваем имя
ASK_DATES    = 1   # Шаг 2: спрашиваем даты
ASK_TOURISTS = 2   # Шаг 3: спрашиваем состав группы
ASK_BUDGET   = 3   # Шаг 4: спрашиваем бюджет
ASK_PHONE    = 4   # Шаг 5: спрашиваем телефон
DONE         = 5   # Финал: благодарим и закрываем


# ── Обработчики каждого шага ──────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Старт диалога.
    Срабатывает когда клиент нажал кнопку "Узнать подробнее"
    или написал /start.
    """
    # Сохраняем данные о туре из параметров команды /start
    # Например: /start turkey_45000_15apr — это ID тура в ссылке из поста
    args = context.args
    tour_info = " ".join(args) if args else "не указан"
    context.user_data["tour_ref"] = tour_info

    await update.message.reply_text(
        "👋 Здравствуйте! Меня зовут Family Tour Bot.\n\n"
        "Вы интересуетесь горящим туром. Чтобы наш менеджер "
        "подобрал лучший вариант именно для вас, ответьте "
        "на несколько коротких вопросов.\n\n"
        "Как вас зовут? (Имя и фамилия)",
        reply_markup=ReplyKeyboardRemove(),  # убираем клавиатуру если была
    )
    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получили имя → спрашиваем даты"""
    name = update.message.text.strip()
    context.user_data["name"] = name

    keyboard = [
        ["Конкретные даты"],
        ["Гибкие даты (любые ближайшие)"],
        ["Могу лететь в любое время"],
    ]
    await update.message.reply_text(
        f"Приятно познакомиться, {name.split()[0]}! 😊\n\n"
        "Какие даты вылета вас интересуют?\n"
        "(Выберите вариант или напишите конкретные даты)",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True,
                                          resize_keyboard=True),
    )
    return ASK_DATES


async def ask_dates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получили даты → спрашиваем состав группы"""
    dates = update.message.text.strip()
    context.user_data["dates"] = dates

    keyboard = [
        ["1 взрослый", "2 взрослых"],
        ["2 взрослых + 1 ребёнок", "2 взрослых + 2 детей"],
        ["Другой состав"],
    ]
    await update.message.reply_text(
        "Понял! 👨‍👩‍👧\n\n"
        "Кто едет? Выберите состав группы:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True,
                                          resize_keyboard=True),
    )
    return ASK_TOURISTS


async def ask_tourists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получили состав → спрашиваем бюджет"""
    tourists = update.message.text.strip()
    context.user_data["tourists"] = tourists

    keyboard = [
        ["До 50 000 ₽/чел", "50 000 — 80 000 ₽/чел"],
        ["80 000 — 120 000 ₽/чел", "Более 120 000 ₽/чел"],
    ]
    await update.message.reply_text(
        "Отлично! 💰\n\n"
        "Примерный бюджет на одного человека:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True,
                                          resize_keyboard=True),
    )
    return ASK_BUDGET


async def ask_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получили бюджет → спрашиваем телефон"""
    budget = update.message.text.strip()
    context.user_data["budget"] = budget

    await update.message.reply_text(
        "Почти готово! 📞\n\n"
        "Укажите ваш номер телефона — менеджер свяжется с вами в течение 1 часа:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Получили телефон — диалог завершён.
    Сохраняем данные и уведомляем руководителя.
    """
    phone = update.message.text.strip()
    if len(re.sub(r"\D", "", phone)) < 10:
        await update.message.reply_text(
            "Пожалуйста, укажите ваш номер телефона цифрами (минимум 10 цифр).\n\n"
            "Например: +7 917 044-21-00"
        )
        return ASK_PHONE
    context.user_data["phone"] = phone

    # Собираем все данные клиента
    user = update.effective_user
    lead = {
        "name":     context.user_data.get("name", "—"),
        "phone":    phone,
        "tour":     context.user_data.get("tour_ref", "не указан"),
        "dates":    context.user_data.get("dates", "—"),
        "tourists": context.user_data.get("tourists", "—"),
        "budget":   context.user_data.get("budget", "—"),
        "tg_id":    str(user.id),
        "tg_user":  f"@{user.username}" if user.username else "нет username",
        "source":   "Telegram бот",
    }

    # ── Сохраняем в Google Sheets ─────────────────────────────
    try:
        sheets = SheetsClient(
            credentials_file=Config.GOOGLE_CREDENTIALS_FILE,
            sheet_id=Config.GOOGLE_SHEET_ID,
        )
        sheets.add_lead(lead)
        print(f"✅ Заявка сохранена в Google Sheets: {lead['name']}")
    except Exception as e:
        # Не падаем если Sheets недоступен — главное уведомить руководителя
        print(f"⚠️  Ошибка сохранения в Sheets: {e}")

    # ── Уведомляем всех админов ───────────────────────────────
    try:
        tg = TelegramPublisher(
            token=Config.TELEGRAM_BOT_TOKEN,
            channel_id=Config.TELEGRAM_CHANNEL_ID,
            admin_id=Config.TELEGRAM_ADMIN_IDS,
        )
        tg.notify_new_lead(
            name=lead["name"],
            phone=lead["phone"],
            tour=lead["tour"],
            dates=lead["dates"],
            tourists=lead["tourists"],
            budget=lead["budget"],
            source=f"Telegram ({lead['tg_user']})",
        )
    except Exception as e:
        print(f"⚠️  Ошибка уведомления руководителя: {e}")

    # ── Благодарим клиента ────────────────────────────────────
    await update.message.reply_text(
        f"✅ Отлично, {lead['name'].split()[0]}!\n\n"
        "Ваша заявка принята. Наш менеджер свяжется с вами "
        "в течение <b>1 часа</b> для подбора лучшего варианта.\n\n"
        "Если хотите — можете написать нам напрямую:\n"
        "📞 +7 (917) 044-21-00\n\n"
        "До встречи на курорте! 🌴",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def send_to_max(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Команда /max USER_ID сообщение — отправляет сообщение клиенту MAX через бота.
    Доступна только админу.
    """
    if update.effective_user.id not in Config.TELEGRAM_ADMIN_IDS:
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Использование: /max USER_ID текст\n\n"
            "Пример: /max 42763058 Здравствуйте! Это менеджер Family Tour."
        )
        return

    try:
        max_user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом")
        return

    text = " ".join(args[1:])
    try:
        import requests as _req
        resp = _req.post(
            "https://platform-api.max.ru/messages",
            headers={"Authorization": Config.MAX_TOKEN},
            params={"user_id": max_user_id},
            json={"text": text, "format": "markdown"},
            timeout=10,
        )
        if resp.status_code < 400:
            # Включаем режим прямой переписки — ответы клиента пойдут админу
            from bot.max_handler import register_admin_chat
            register_admin_chat(max_user_id)
            await update.message.reply_text(
                f"✅ Сообщение доставлено клиенту MAX (id={max_user_id}).\n"
                f"Его ответ придёт сюда же."
            )
        else:
            await update.message.reply_text(f"❌ Ошибка MAX API {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось отправить: {e}")


async def trigger_news_collection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /news — запустить сбор новостей вручную, не дожидаясь 8:00."""
    if update.effective_user.id not in Config.TELEGRAM_ADMIN_IDS:
        return
    await update.message.reply_text("📰 Запускаю сбор новостей, это займёт 1-2 минуты...")
    try:
        from main import collect_news_job
        await collect_news_job(context)
        await update.message.reply_text("Готово. Если новости были — пришлю превью отдельным сообщением.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Клиент написал /cancel — прерываем диалог"""
    await update.message.reply_text(
        "Хорошо, отменяем. Если понадобится помощь — "
        "напишите нам или начните заново командой /start",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ── Публикация поста во все каналы (TG / VK / MAX) ────────────

async def publish_to_channels(bot, post_text: str,
                               photo_url: str = None,
                               photo_bytes: bytes = None,
                               tour_id: str = "",
                               overlay_country: str = "",
                               overlay_price: str = "",
                               overlay_departure: str = "") -> tuple[bool, str]:
    """
    Публикует пост в Telegram-канал, ВК и MAX.
    Если заданы overlay_* — накладывает текст на фото.
    Возвращает (успех, сообщение_о_статусе).
    """
    from io import BytesIO

    tg_photo_content = None
    if photo_bytes:
        tg_photo_content = photo_bytes
    elif photo_url:
        try:
            import requests as _req
            resp = _req.get(photo_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                tg_photo_content = resp.content
        except Exception:
            pass

    # Наложение текста на фото
    if tg_photo_content and overlay_country:
        try:
            from image_overlay import add_tour_overlay
            tg_photo_content = add_tour_overlay(
                tg_photo_content,
                country=overlay_country,
                price=overlay_price,
                departure=overlay_departure,
            )
            logger.info(f"Overlay: добавлен текст на фото (страна={overlay_country!r}, цена={overlay_price!r})")
        except Exception as oe:
            logger.warning(f"Overlay не применён: {oe}")

    try:
        if tg_photo_content:
            await bot.send_photo(
                chat_id=Config.TELEGRAM_CHANNEL_ID,
                photo=BytesIO(tg_photo_content),
                caption=post_text,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(
                chat_id=Config.TELEGRAM_CHANNEL_ID,
                text=post_text,
                parse_mode="HTML",
            )

        if Config.VK_TOKEN and Config.VK_GROUP_ID:
            try:
                vk = VKPublisher(token=Config.VK_TOKEN, group_id=Config.VK_GROUP_ID)
                vk.publish(post_text, photo_url=photo_url or None, photo_bytes=tg_photo_content)
            except Exception as vk_err:
                logger.warning(f"⚠️ VK публикация не удалась: {vk_err}")

        if Config.MAX_TOKEN and Config.MAX_CHAT_ID:
            try:
                max_pub = MAXPublisher(token=Config.MAX_TOKEN, chat_id=Config.MAX_CHAT_ID)
                max_pub.publish(post_text, photo_url or None)
            except Exception as max_err:
                logger.warning(f"⚠️ MAX публикация не удалась: {max_err}")

        if tour_id.startswith("sheets_"):
            try:
                row_num = int(tour_id.replace("sheets_", ""))
                sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
                sheets.mark_tour_status(row_num, "ОПУБЛИКОВАН")
            except Exception as se:
                logger.warning(f"Sheets update failed: {se}")

        return True, "\n\n✅ <b>ОПУБЛИКОВАНО!</b>"

    except Exception as e:
        return False, f"\n\n❌ Ошибка: {e}"


def _next_schedule_slot(now_utc=None) -> "datetime":
    """
    Возвращает ближайший слот публикации (9/14/19 МСК) в UTC.
    Если сейчас между слотами — следующий. Если после 19:00 — завтра 9:00.
    """
    from datetime import datetime, timedelta, timezone
    msk = timezone(timedelta(hours=3))
    now = (now_utc or datetime.now(timezone.utc)).astimezone(msk)
    for hour in Config.PUBLISH_HOURS:
        slot = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if slot > now:
            return slot.astimezone(timezone.utc)
    # Все слоты на сегодня прошли — завтра первый
    tomorrow = now + timedelta(days=1)
    slot = tomorrow.replace(hour=Config.PUBLISH_HOURS[0], minute=0, second=0, microsecond=0)
    return slot.astimezone(timezone.utc)


# ── Обработчик кнопок одобрения (для руководителя) ───────────

async def _handle_approval(update: Update,
                            context: ContextTypes.DEFAULT_TYPE,
                            pending_posts: dict,
                            save_pending=None,
                            scheduled_posts: list = None,
                            save_scheduled=None) -> None:
    """
    Обрабатывает нажатие кнопок ✅/❌ в превью поста.
    Срабатывает только у руководителя (проверяем admin_id).
    """
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in Config.TELEGRAM_ADMIN_IDS:
        await query.answer("⛔ Только для администратора", show_alert=True)
        return

    data = query.data
    msg = query.message

    # Защита от двойной публикации: проверяем метку в сообщении
    existing_text = (msg.caption_html or msg.text_html or msg.caption or msg.text or "")
    if "ОПУБЛИКОВАНО" in existing_text or "ПРОПУЩЕН" in existing_text:
        await query.answer("Уже обработано другим админом", show_alert=True)
        return

    # Общий блок для approve и schedule — восстановление текста и фото
    if data.startswith("approve_") or data.startswith("schedule_"):
        prefix_len = len("approve_") if data.startswith("approve_") else len("schedule_")
        tour_id = data[prefix_len:]

        stored = pending_posts.get(tour_id, {})
        post_text = stored.get("text", "")
        photo_url = stored.get("photo_url", "")
        photo_bytes = None
        ov_country   = stored.get("overlay_country", "")
        ov_price     = stored.get("overlay_price", "")
        ov_departure = stored.get("overlay_departure", "")

        if not post_text:
            raw = msg.caption_html or msg.text_html or ""
            for p in [
                "📋 <b>НОВЫЙ ГОРЯЩИЙ ТУР — на одобрение:</b>\n\n",
                "📋 НОВЫЙ ГОРЯЩИЙ ТУР — на одобрение:\n\n",
            ]:
                if p in raw:
                    raw = raw.split(p, 1)[1]
                    break
            post_text = raw.strip()
            logger.info(f"PENDING_POSTS пуст — текст восстановлен ({len(post_text)} символов)")

        if not photo_url and msg.photo:
            try:
                tg_file = await msg.photo[-1].get_file()
                photo_bytes = bytes(await tg_file.download_as_bytearray())
                logger.info(f"Фото восстановлено из Telegram ({len(photo_bytes)} байт)")
            except Exception as e:
                logger.warning(f"Не удалось скачать фото: {e}")

    if data.startswith("approve_"):
        logger.info(f"Approve: photo_url={bool(photo_url)}, photo_bytes={bool(photo_bytes)}")
        ok, status_line = await publish_to_channels(
            context.bot, post_text,
            photo_url=photo_url or None,
            photo_bytes=photo_bytes,
            tour_id=tour_id,
            overlay_country=ov_country,
            overlay_price=ov_price,
            overlay_departure=ov_departure,
        )
        pending_posts.pop(tour_id, None)
        if save_pending:
            save_pending(pending_posts)

        try:
            if msg.photo:
                await query.edit_message_caption(
                    caption=(msg.caption or "") + status_line, parse_mode="HTML")
            else:
                await query.edit_message_text(
                    text=(msg.text or "") + status_line, parse_mode="HTML")
        except Exception:
            pass

    elif data.startswith("schedule_"):
        import base64
        from datetime import datetime, timezone, timedelta
        slot = _next_schedule_slot()
        msk_slot = slot.astimezone(timezone(timedelta(hours=3)))

        entry = {
            "tour_id":            tour_id,
            "text":               post_text,
            "photo_url":          photo_url or "",
            "photo_b64":          base64.b64encode(photo_bytes).decode() if photo_bytes else "",
            "overlay_country":    ov_country,
            "overlay_price":      ov_price,
            "overlay_departure":  ov_departure,
            "scheduled_for":      slot.isoformat(),
            "scheduled_for_msk":  msk_slot.strftime('%d.%m %H:%M МСК'),
            "country":            ov_country,
            "price":              ov_price,
            "date":               ov_departure,
        }

        # Сохраняем в Google Sheets — переживёт перезапуск Railway
        try:
            sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
            sheets.add_scheduled_post(entry)
            logger.info(f"Расписание: добавлен пост tour_id={tour_id} на {msk_slot.strftime('%d.%m %H:%M МСК')}")
        except Exception as e:
            logger.warning(f"Не удалось сохранить расписание в Sheets: {e}")

        # Дублируем в локальную очередь — для совместимости и быстрого доступа
        if scheduled_posts is not None:
            scheduled_posts.append(entry)
            if save_scheduled:
                save_scheduled(scheduled_posts)

        pending_posts.pop(tour_id, None)
        if save_pending:
            save_pending(pending_posts)

        status_line = f"\n\n⏰ <b>Запланирован на {msk_slot.strftime('%d.%m %H:%M')} МСК</b>"
        try:
            if msg.photo:
                await query.edit_message_caption(
                    caption=(msg.caption or "") + status_line, parse_mode="HTML")
            else:
                await query.edit_message_text(
                    text=(msg.text or "") + status_line, parse_mode="HTML")
        except Exception:
            pass

    elif data.startswith("reject_"):
        tour_id = data.replace("reject_", "")
        pending_posts.pop(tour_id, None)
        if save_pending:
            save_pending(pending_posts)
        status_line = "\n\n❌ <b>ПРОПУЩЕН</b>"
        try:
            if msg.photo:
                await query.edit_message_caption(
                    caption=(msg.caption or "") + status_line, parse_mode="HTML")
            else:
                await query.edit_message_text(
                    text=(msg.text or "") + status_line, parse_mode="HTML")
        except Exception:
            pass


def build_application(pending_posts: dict = None, save_pending=None,
                      scheduled_posts: list = None, save_scheduled=None) -> Application:
    """
    Собирает и настраивает Telegram-бота.
    scheduled_posts — очередь запланированных постов (list из main.py)
    save_scheduled  — функция сохранения очереди в файл
    """
    _pending = pending_posts if pending_posts is not None else {}
    _save = save_pending or (lambda d: None)
    _sched = scheduled_posts if scheduled_posts is not None else []
    _save_sched = save_scheduled or (lambda d: None)

    async def handle_approval_with_store(update: Update,
                                          context: ContextTypes.DEFAULT_TYPE) -> None:
        await _handle_approval(update, context, _pending, _save, _sched, _save_sched)

    import os
    from telegram.request import HTTPXRequest
    proxy = os.getenv("TELEGRAM_PROXY", "").strip()
    if proxy:
        request = HTTPXRequest(proxy=proxy, connect_timeout=30, read_timeout=30, write_timeout=30)
        app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).request(request).build()
    else:
        app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).connect_timeout(30).read_timeout(30).write_timeout(30).build()

    # Диалог сбора заявок
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_DATES:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_dates)],
            ASK_TOURISTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tourists)],
            ASK_BUDGET:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_budget)],
            ASK_PHONE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)

    # Команда /max для отправки сообщений клиентам MAX
    app.add_handler(CommandHandler("max", send_to_max))

    # Команда /news для ручного сбора новостей
    app.add_handler(CommandHandler("news", trigger_news_collection))

    # Обработчик кнопок одобрения (для руководителя)
    app.add_handler(CallbackQueryHandler(handle_approval_with_store, pattern="^(approve|reject|schedule)_"))

    return app
