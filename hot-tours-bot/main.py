"""
main.py — Точка входа. Запускает всю систему.

Что делает:
  1. Каждые 4 часа (09:30, 14:00, 19:00) ищет горящие туры
  2. Генерирует красивый пост через ИИ
  3. Публикует в ВК, Telegram, MAX
  4. Параллельно держит бота для сбора заявок

Запуск:  python main.py
"""

import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import Config
from tourvisor.client import TourvisorClient
from ai.generator import generate_post, generate_post_without_ai
from publisher.telegram import TelegramPublisher
from publisher.vk import VKPublisher
from bot.handler import build_application

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Флаг режима: True = присылать на одобрение, False = автопилот ──
APPROVAL_MODE = True   # первую неделю держим True


async def publish_hot_tour():
    """
    Главная задача по расписанию.
    Ищет тур → генерирует пост → публикует или отправляет на одобрение.
    """
    logger.info("⏰ Запускаю поиск горящих туров...")

    tv = TourvisorClient(Config.TOURVISOR_LOGIN, Config.TOURVISOR_PASSWORD)
    ufa_id = tv.find_city_id("Уфа")
    if not ufa_id:
        logger.error("Не нашли код Уфы")
        return

    tours = tv.find_hot_tours(
        departure_id=ufa_id,
        nights_from=Config.NIGHTS_FROM,
        nights_to=Config.NIGHTS_TO,
        days_ahead=Config.DAYS_AHEAD,
        price_max=Config.MAX_PRICE,
    )

    if not tours:
        logger.warning("Горящих туров не найдено")
        return

    tour = tours[0]
    logger.info(f"Выбран тур: {tour.hotel_name}, {tour.country}, {tour.formatted_price_per_person}/чел")

    # Генерируем текст
    if Config.ANTHROPIC_API_KEY:
        text = generate_post(tour, Config.ANTHROPIC_API_KEY)
    else:
        text = generate_post_without_ai(tour)

    tg = TelegramPublisher(
        token=Config.TELEGRAM_BOT_TOKEN,
        channel_id=Config.TELEGRAM_CHANNEL_ID,
        admin_id=Config.TELEGRAM_ADMIN_ID,
    )

    if APPROVAL_MODE:
        # Режим одобрения: отправляем руководителю
        tg.send_approval_request(text, tour.photo_url, tour.tour_id)
        logger.info("Пост отправлен руководителю на одобрение")
    else:
        # Автопилот: публикуем сразу везде
        if Config.TELEGRAM_BOT_TOKEN:
            tg.publish(text, tour.photo_url)

        if Config.VK_TOKEN and Config.VK_GROUP_ID:
            vk = VKPublisher(token=Config.VK_TOKEN, group_id=Config.VK_GROUP_ID)
            vk.publish(text, tour.photo_url)

        tg.notify_admin(
            f"✅ Автоматически опубликован тур:\n"
            f"{tour.hotel_name}, {tour.country}\n"
            f"{tour.formatted_price_per_person}/чел, вылет {tour.date_from}"
        )
        logger.info("Пост опубликован в автопилоте")


async def main():
    """Запускает планировщик и Telegram-бота"""

    logger.info("🚀 Family Tour Bot запускается...")

    # Планировщик публикаций
    scheduler = AsyncIOScheduler()
    for hour in Config.PUBLISH_HOURS:
        scheduler.add_job(
            publish_hot_tour,
            CronTrigger(hour=hour, minute=30),  # в XX:30
            id=f"publish_{hour}",
        )
    scheduler.start()
    logger.info(f"✅ Планировщик запущен: публикации в {Config.PUBLISH_HOURS}")

    # Запускаем Telegram-бота
    app = build_application()
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("✅ Telegram-бот запущен")

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
