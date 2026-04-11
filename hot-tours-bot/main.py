"""
main.py — Точка входа. Запускает всю систему.

Что делает:
  1. Каждые 5 минут проверяет лист "Туры к публикации" в Google Sheets
  2. Находит туры со статусом НОВЫЙ → генерирует пост → публикует
  3. Параллельно держит Telegram-бота для сбора заявок от клиентов

Источник туров: Google Sheets (лист "Туры к публикации")
  - Менеджер вносит тур вручную (2 минуты)
  - Бот публикует автоматически, меняет статус на ОПУБЛИКОВАН

Запуск:  python main.py
"""

import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

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
APPROVAL_MODE = True   # первую неделю держим True


async def publish_from_sheets():
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

        # Сразу ставим ПУБЛИКУЕТСЯ — чтобы не задублировать при следующей проверке
        sheets.mark_tour_publishing(row_num)

        try:
            # Генерируем текст поста
            logger.info(f"  Генерирую пост: {name}")
            text = generate_post_from_dict(tour_row, Config.ANTHROPIC_API_KEY)
            photo_url = tour_row.get("Фото URL", "").strip() or None

            tg = TelegramPublisher(
                token=Config.TELEGRAM_BOT_TOKEN,
                channel_id=Config.TELEGRAM_CHANNEL_ID,
                admin_id=Config.TELEGRAM_ADMIN_ID,
            )

            if APPROVAL_MODE:
                # Режим одобрения: шлём руководителю превью
                tour_id = f"sheets_{row_num}"
                tg.send_approval_request(text, photo_url, tour_id)
                sheets.mark_tour_status(row_num, "НА ОДОБРЕНИИ",
                                         published_at=datetime.now().strftime("%d.%m.%Y %H:%M"))
                logger.info(f"  📨 Отправлено на одобрение: {name}")
            else:
                # Автопилот: публикуем сразу везде
                if Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHANNEL_ID:
                    tg.publish(text, photo_url)
                    logger.info(f"  ✅ Telegram: {name}")

                if Config.VK_TOKEN and Config.VK_GROUP_ID:
                    vk = VKPublisher(token=Config.VK_TOKEN, group_id=Config.VK_GROUP_ID)
                    vk.publish(text, photo_url)
                    logger.info(f"  ✅ ВК: {name}")

                sheets.mark_tour_status(
                    row_num, "ОПУБЛИКОВАН",
                    published_at=datetime.now().strftime("%d.%m.%Y %H:%M")
                )
                tg.notify_admin(f"✅ Опубликован тур:\n{name}\n{text[:200]}...")

            # Небольшая пауза между публикациями если несколько туров
            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"  ❌ Ошибка публикации {name}: {e}")
            sheets.mark_tour_status(row_num, "ОШИБКА", error=str(e))


async def main():
    """Запускает планировщик и Telegram-бота"""

    logger.info("🚀 Family Tour Bot запускается...")
    logger.info(f"   Режим: {'одобрение' if APPROVAL_MODE else 'автопилот'}")

    # ── Планировщик: проверяем Sheets каждые 5 минут ──
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        publish_from_sheets,
        IntervalTrigger(minutes=5),
        id="sheets_check",
    )
    scheduler.start()
    logger.info("✅ Планировщик запущен: проверка Sheets каждые 5 минут")

    # ── Telegram-бот для сбора заявок ──
    app = build_application()
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("✅ Telegram-бот запущен (сбор заявок от клиентов)")

    # Держим процесс живым
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("⏹ Останавливаюсь...")
    finally:
        scheduler.shutdown()
        await app.updater.stop()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
