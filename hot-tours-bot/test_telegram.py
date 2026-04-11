"""
test_telegram.py — Проверка публикации в Telegram-канал

Запусти: python test_telegram.py
Ожидаемый результат: тестовый пост появится в канале @familytour_ufa
"""

import asyncio
from config import Config
from ai.generator import generate_post_from_dict

TEST_TOUR = {
    "Страна":       "Турция",
    "Курорт":       "Анталья",
    "Отель":        "Rixos Premium Belek",
    "Звёзды":       "5",
    "Питание":      "Ultra All Inclusive",
    "Дата вылета":  "20.04.2026",
    "Ночей":        "7",
    "Цена/чел":     "45000",
    # Вставь сюда реальную ссылку на фото отеля
    # Можно взять с сайта отеля или из поиска Google → Картинки → Правая кнопка → Копировать адрес изображения
    "Фото URL":     "https://cf.bstatic.com/xdata/images/hotel/max1280x900/263150894.jpg",
}

print("=" * 60)
print("  ТЕСТ: Публикация в Telegram-канал")
print("=" * 60)

print(f"\nКанал: {Config.TELEGRAM_CHANNEL_ID}")
print(f"Бот токен: {Config.TELEGRAM_BOT_TOKEN[:20]}...")

# Генерируем пост
print("\n📝 Генерирую тестовый пост...")
text = generate_post_from_dict(TEST_TOUR)
print("Пост готов:\n")
print(text)
print()

# Публикуем в Telegram
async def send():
    from telegram import Bot
    bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
    photo_url = TEST_TOUR.get("Фото URL", "").strip()
    try:
        if photo_url:
            msg = await bot.send_photo(
                chat_id=Config.TELEGRAM_CHANNEL_ID,
                photo=photo_url,
                caption=text,
            )
        else:
            msg = await bot.send_message(
                chat_id=Config.TELEGRAM_CHANNEL_ID,
                text=text,
            )
        print(f"✅ Пост опубликован в канале!")
        print(f"   ID сообщения: {msg.message_id}")
        print(f"   Открой @familytour_ufa и проверь")
    except Exception as e:
        print(f"❌ Ошибка публикации: {e}")
        print(f"   Возможно фото недоступно — попробуй другую ссылку")

asyncio.run(send())
