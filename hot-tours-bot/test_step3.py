"""
test_step3.py — Публикация тестового поста в Telegram и ВК

Запусти: python test_step3.py
Ожидаемый результат: пост появляется в твоём Telegram-канале и/или в ВК

Требует в .env:
  - TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TELEGRAM_ADMIN_ID
  - VK_TOKEN, VK_GROUP_ID (опционально)
  - TOURVISOR_LOGIN, TOURVISOR_PASSWORD
"""

from config import Config
from tourvisor.client import TourvisorClient
from ai.generator import generate_post, generate_post_without_ai
from publisher.telegram import TelegramPublisher
from publisher.vk import VKPublisher


def main():
    print("=" * 60)
    print("  ШАГ 3: Публикация поста в Telegram и ВК")
    print("=" * 60)

    # ── Получаем тур ──────────────────────────────────────────
    print("\n🔍 Получаем тур для публикации...")
    tv = TourvisorClient(Config.TOURVISOR_LOGIN, Config.TOURVISOR_PASSWORD)
    ufa_id = tv.find_city_id("Уфа")
    tours = tv.find_hot_tours(departure_id=ufa_id, days_ahead=14,
                               price_max=Config.MAX_PRICE)
    if not tours:
        print("❌ Туры не найдены")
        return

    tour = tours[0]
    print(f"✅ Тур: {tour.hotel_name}, {tour.country}")

    # ── Генерируем текст поста ────────────────────────────────
    print("\n📝 Генерируем текст поста...")
    if Config.ANTHROPIC_API_KEY:
        text = generate_post(tour, Config.ANTHROPIC_API_KEY)
        print("✅ Текст сгенерирован через Claude AI")
    else:
        text = generate_post_without_ai(tour)
        print("✅ Текст сгенерирован по шаблону (без AI)")

    # ── Тест Telegram ─────────────────────────────────────────
    print("\n📱 Тест: публикация в Telegram")
    print("-" * 40)

    if not Config.TELEGRAM_BOT_TOKEN:
        print("⚠️  TELEGRAM_BOT_TOKEN не задан — пропускаем")
    else:
        tg = TelegramPublisher(
            token=Config.TELEGRAM_BOT_TOKEN,
            channel_id=Config.TELEGRAM_CHANNEL_ID,
            admin_id=Config.TELEGRAM_ADMIN_ID,
        )

        # Сначала отправляем себе на одобрение
        print("  Отправляю превью тебе на одобрение...")
        tg.send_approval_request(text, tour.photo_url, tour.tour_id)

        # Потом публикуем в канал
        print(f"  Публикую в канал {Config.TELEGRAM_CHANNEL_ID}...")
        msg_id = tg.publish(text, tour.photo_url)
        if msg_id:
            print(f"  ✅ Опубликовано! Проверь Telegram-канал.")

    # ── Тест ВКонтакте ────────────────────────────────────────
    print("\n📘 Тест: публикация в ВКонтакте")
    print("-" * 40)

    if not Config.VK_TOKEN or not Config.VK_GROUP_ID:
        print("⚠️  VK_TOKEN или VK_GROUP_ID не заданы — пропускаем")
        print("   Добавь в .env чтобы публиковать в ВК")
    else:
        vk = VKPublisher(token=Config.VK_TOKEN, group_id=Config.VK_GROUP_ID)
        print(f"  Публикую в группу ID={Config.VK_GROUP_ID}...")
        post_id = vk.publish(text, tour.photo_url)
        if post_id:
            print(f"  ✅ Опубликовано! Проверь vk.com/family_toor")

    print("\n" + "=" * 60)
    print("🎉 Шаг 3 пройден! Публикатор работает.")
    print("Следующий шаг: python test_step4.py")
    print("(Telegram-бот для сбора заявок)")
    print("=" * 60)


if __name__ == "__main__":
    main()
