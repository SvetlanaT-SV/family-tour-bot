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

    # ── Уведомляем руководителя ───────────────────────────────
    try:
        tg = TelegramPublisher(
            token=Config.TELEGRAM_BOT_TOKEN,
            channel_id=Config.TELEGRAM_CHANNEL_ID,
            admin_id=Config.TELEGRAM_ADMIN_ID,
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


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Клиент написал /cancel — прерываем диалог"""
    await update.message.reply_text(
        "Хорошо, отменяем. Если понадобится помощь — "
        "напишите нам или начните заново командой /start",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ── Обработчик кнопок одобрения (для руководителя) ───────────

async def _handle_approval(update: Update,
                            context: ContextTypes.DEFAULT_TYPE,
                            pending_posts: dict,
                            save_pending=None) -> None:
    """
    Обрабатывает нажатие кнопок ✅/❌ в превью поста.
    Срабатывает только у руководителя (проверяем admin_id).
    """
    query = update.callback_query
    await query.answer()

    if query.from_user.id != Config.TELEGRAM_ADMIN_ID:
        await query.answer("⛔ Только для администратора", show_alert=True)
        return

    data = query.data
    msg = query.message

    if data.startswith("approve_"):
        tour_id = data.replace("approve_", "")

        # Берём оригинальный пост с HTML из хранилища
        stored = pending_posts.get(tour_id, {})
        post_text = stored.get("text", "")
        photo_url = stored.get("photo_url", "")
        photo_bytes = None

        # Если после перезапуска хранилище пустое — восстанавливаем из сообщения
        if not post_text:
            raw = msg.caption_html or msg.text_html or ""
            for prefix in [
                "📋 <b>НОВЫЙ ГОРЯЩИЙ ТУР — на одобрение:</b>\n\n",
                "📋 НОВЫЙ ГОРЯЩИЙ ТУР — на одобрение:\n\n",
            ]:
                if prefix in raw:
                    raw = raw.split(prefix, 1)[1]
                    break
            post_text = raw.strip()
            logger.info(f"PENDING_POSTS пуст — текст восстановлен ({len(post_text)} символов)")

        # Если фото URL нет, но в сообщении есть фото — скачиваем с Telegram
        if not photo_url and msg.photo:
            try:
                tg_file = await msg.photo[-1].get_file()
                photo_bytes = bytes(await tg_file.download_as_bytearray())
                logger.info(f"Фото восстановлено из Telegram ({len(photo_bytes)} байт)")
            except Exception as e:
                logger.warning(f"Не удалось скачать фото из Telegram: {e}")

        logger.info(f"Approve: photo_url={bool(photo_url)}, photo_bytes={bool(photo_bytes)}")

        try:
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

            if tg_photo_content:
                await context.bot.send_photo(
                    chat_id=Config.TELEGRAM_CHANNEL_ID,
                    photo=BytesIO(tg_photo_content),
                    caption=post_text,
                    parse_mode="HTML",
                )
            else:
                await context.bot.send_message(
                    chat_id=Config.TELEGRAM_CHANNEL_ID,
                    text=post_text,
                    parse_mode="HTML",
                )

            # Публикуем в ВКонтакте
            logger.info(f"VK: token={bool(Config.VK_TOKEN)}, group_id={Config.VK_GROUP_ID}")
            if Config.VK_TOKEN and Config.VK_GROUP_ID:
                try:
                    vk = VKPublisher(token=Config.VK_TOKEN, group_id=Config.VK_GROUP_ID)
                    vk_result = vk.publish(
                        post_text,
                        photo_url=photo_url or None,
                        photo_bytes=tg_photo_content,
                    )
                    logger.info(f"VK: результат публикации={vk_result}")
                except Exception as vk_err:
                    logger.warning(f"⚠️ VK публикация не удалась: {vk_err}")

            # Публикуем в MAX
            logger.info(f"MAX: token={bool(Config.MAX_TOKEN)}, chat_id={Config.MAX_CHAT_ID}")
            if Config.MAX_TOKEN and Config.MAX_CHAT_ID:
                try:
                    max_pub = MAXPublisher(token=Config.MAX_TOKEN, chat_id=Config.MAX_CHAT_ID)
                    result = max_pub.publish(post_text, photo_url or None)
                    logger.info(f"MAX: результат публикации={result}")
                except Exception as max_err:
                    logger.warning(f"⚠️ MAX публикация не удалась: {max_err}")

            # Обновляем статус в Sheets
            if tour_id.startswith("sheets_"):
                row_num = int(tour_id.replace("sheets_", ""))
                sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
                sheets.mark_tour_status(row_num, "ОПУБЛИКОВАН")

            pending_posts.pop(tour_id, None)
            if save_pending:
                save_pending(pending_posts)
            status_line = "\n\n✅ <b>ОПУБЛИКОВАНО!</b>"
        except Exception as e:
            status_line = f"\n\n❌ Ошибка: {e}"

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


def build_application(pending_posts: dict = None, save_pending=None) -> Application:
    """
    Собирает и настраивает Telegram-бота.
    Вызывается из main.py при старте.
    save_pending — функция сохранения PENDING_POSTS в файл (из main.py).
    """
    _pending = pending_posts if pending_posts is not None else {}
    _save = save_pending or (lambda d: None)

    async def handle_approval_with_store(update: Update,
                                          context: ContextTypes.DEFAULT_TYPE) -> None:
        await _handle_approval(update, context, _pending, _save)

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

    # Обработчик кнопок одобрения (для руководителя)
    app.add_handler(CallbackQueryHandler(handle_approval_with_store, pattern="^(approve|reject)_"))

    return app
