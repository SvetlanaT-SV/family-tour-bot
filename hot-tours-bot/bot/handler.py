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
ASK_PHONE    = 1   # Шаг 2: спрашиваем телефон
ASK_DATES    = 2   # Шаг 3: спрашиваем даты
ASK_TOURISTS = 3   # Шаг 4: спрашиваем состав группы
ASK_BUDGET   = 4   # Шаг 5: спрашиваем бюджет
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
    """Получили имя → спрашиваем телефон"""
    name = update.message.text.strip()
    context.user_data["name"] = name

    await update.message.reply_text(
        f"Приятно познакомиться, {name.split()[0]}! 😊\n\n"
        "Укажите ваш номер телефона — менеджер свяжется с вами "
        "в течение 1 часа:",
    )
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получили телефон → спрашиваем даты"""
    phone = update.message.text.strip()
    context.user_data["phone"] = phone

    # Кнопки для выбора гибкости дат
    keyboard = [
        ["Конкретные даты"],
        ["Гибкие даты (любые ближайшие)"],
        ["Могу лететь в любое время"],
    ]
    await update.message.reply_text(
        "Отлично! 📅\n\n"
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
        "Почти готово! 💰\n\n"
        "Примерный бюджет на одного человека:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True,
                                          resize_keyboard=True),
    )
    return ASK_BUDGET


async def ask_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Получили бюджет — диалог завершён.
    Сохраняем данные и уведомляем руководителя.
    """
    budget = update.message.text.strip()
    context.user_data["budget"] = budget

    # Собираем все данные клиента
    user = update.effective_user
    lead = {
        "name":     context.user_data.get("name", "—"),
        "phone":    context.user_data.get("phone", "—"),
        "tour":     context.user_data.get("tour_ref", "не указан"),
        "dates":    context.user_data.get("dates", "—"),
        "tourists": context.user_data.get("tourists", "—"),
        "budget":   budget,
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
        "📞 +7 (XXX) XXX-XX-XX\n\n"
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

async def handle_approval(update: Update,
                            context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает нажатие кнопок ✅/❌ в превью поста.
    Срабатывает только у руководителя (проверяем admin_id).
    """
    query = update.callback_query
    await query.answer()  # убираем индикатор загрузки на кнопке

    # Проверяем что нажал именно руководитель
    if query.from_user.id != Config.TELEGRAM_ADMIN_ID:
        await query.answer("⛔ Только для администратора", show_alert=True)
        return

    data = query.data  # например "approve_tour123" или "reject_tour123"

    if data.startswith("approve_"):
        tour_id = data.replace("approve_", "")

        # Берём текст поста из сообщения (убираем шапку «НОВЫЙ ГОРЯЩИЙ ТУР — на одобрение:»)
        msg = query.message
        raw = msg.caption or msg.text or ""
        # Убираем первую строку-заголовок
        lines = raw.split("\n", 3)
        post_text = lines[3].strip() if len(lines) > 3 else raw

        try:
            if msg.photo:
                await context.bot.send_photo(
                    chat_id=Config.TELEGRAM_CHANNEL_ID,
                    photo=msg.photo[-1].file_id,
                    caption=post_text,
                    parse_mode="HTML",
                )
            else:
                await context.bot.send_message(
                    chat_id=Config.TELEGRAM_CHANNEL_ID,
                    text=post_text,
                    parse_mode="HTML",
                )

            # Обновляем статус в Sheets
            if tour_id.startswith("sheets_"):
                row_num = int(tour_id.replace("sheets_", ""))
                sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
                sheets.mark_tour_status(row_num, "ОПУБЛИКОВАН")

            caption_update = (msg.caption or msg.text or "") + "\n\n✅ <b>ОПУБЛИКОВАНО!</b>"
        except Exception as e:
            caption_update = (msg.caption or msg.text or "") + f"\n\n❌ Ошибка публикации: {e}"

        try:
            if msg.photo:
                await query.edit_message_caption(caption=caption_update, parse_mode="HTML")
            else:
                await query.edit_message_text(text=caption_update, parse_mode="HTML")
        except Exception:
            pass

    elif data.startswith("reject_"):
        tour_id = data.replace("reject_", "")
        msg = query.message
        caption_update = (msg.caption or msg.text or "") + "\n\n❌ <b>ПРОПУЩЕН</b>"
        try:
            if msg.photo:
                await query.edit_message_caption(caption=caption_update, parse_mode="HTML")
            else:
                await query.edit_message_text(text=caption_update, parse_mode="HTML")
        except Exception:
            pass


def build_application() -> Application:
    """
    Собирает и настраивает Telegram-бота.
    Вызывается из main.py при старте.
    """
    app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()

    # Диалог сбора заявок
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_PHONE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            ASK_DATES:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_dates)],
            ASK_TOURISTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tourists)],
            ASK_BUDGET:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_budget)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)

    # Обработчик кнопок одобрения (для руководителя)
    app.add_handler(CallbackQueryHandler(handle_approval, pattern="^(approve|reject)_"))

    return app
