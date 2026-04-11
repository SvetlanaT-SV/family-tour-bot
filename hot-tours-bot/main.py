"""
main.py — Точка входа. Запускает всю систему.

Что делает:
  1. Каждые 5 минут проверяет лист "Туры к публикации" в Google Sheets
  2. Находит туры со статусом НОВЫЙ → генерирует пост → публикует
  3. Параллельно держит Telegram-бота для сбора заявок от клиентов

Запуск:  python main.py
"""

import logging
from datetime import datetime
from telegram.ext import Application, ContextTypes

from config import Config
from ai.generator import generate_post_from_dict
from publisher.telegram import TelegramPublisher
from publisher.vk import VKPublisher
from sheets.client import SheetsClient
from bot.handler import build_application

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Флаг режима: True = присылать на одобрение, False = автопилот ──
APPROVAL_MODE = True


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
            text = generate_post_from_dict(tour_row, Config.ANTHROPIC_API_KEY)
            photo_url = tour_row.get("Фото URL", "").strip() or None

            tg = TelegramPublisher(
                token=Config.TELEGRAM_BOT_TOKEN,
                channel_id=Config.TELEGRAM_CHANNEL_ID,
                admin_id=Config.TELEGRAM_ADMIN_ID,
            )

            if APPROVAL_MODE:
                tour_id = f"sheets_{row_num}"
                tg.send_approval_request(text, photo_url, tour_id)
                sheets.mark_tour_status(row_num, "НА ОДОБРЕНИИ",
                                         published_at=datetime.now().strftime("%d.%m.%Y %H:%M"))
                logger.info(f"  📨 Отправлено на одобрение: {name}")
            else:
                if Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHANNEL_ID:
                    tg.publish(text, photo_url)

                if Config.VK_TOKEN and Config.VK_GROUP_ID:
                    vk = VKPublisher(token=Config.VK_TOKEN, group_id=Config.VK_GROUP_ID)
                    vk.publish(text, photo_url)

                sheets.mark_tour_status(
                    row_num, "ОПУБЛИКОВАН",
                    published_at=datetime.now().strftime("%d.%m.%Y %H:%M")
                )
                tg.notify_admin(f"✅ Опубликован тур:\n{name}\n{text[:200]}...")

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
        interval=300,   # каждые 300 секунд = 5 минут
        first=10,       # первый запуск через 10 секунд после старта
        name="sheets_check",
    )
    logger.info("✅ Планировщик запущен: проверка Sheets каждые 5 минут")


if __name__ == "__main__":
    app = build_application()
    app.post_init = post_init

    logger.info("✅ Telegram-бот запущен")
    app.run_polling(drop_pending_updates=True)
