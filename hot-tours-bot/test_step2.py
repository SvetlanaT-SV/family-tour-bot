"""
test_step2.py — Проверка генерации текста поста

Запусти: python test_step2.py
Ожидаемый результат: готовый текст поста как он будет выглядеть в ВК/Telegram

Требует: TOURVISOR_LOGIN, TOURVISOR_PASSWORD, ANTHROPIC_API_KEY в .env
Если нет Claude API ключа — покажет шаблонный вариант без ИИ.
"""

from config import Config
from tourvisor.client import TourvisorClient
from ai.generator import generate_post, generate_post_without_ai


def main():
    print("=" * 60)
    print("  ШАГ 2: Генерация текста поста")
    print("=" * 60)

    # ── Получаем один тур для теста ───────────────────────────
    client = TourvisorClient(Config.TOURVISOR_LOGIN, Config.TOURVISOR_PASSWORD)

    print("\n🔍 Ищем один тур для теста...")
    ufa_id = client.find_city_id("Уфа")
    if not ufa_id:
        print("❌ Не нашли Уфу. Сначала запусти test_step1.py")
        return

    tours = client.find_hot_tours(
        departure_id=ufa_id,
        nights_from=7,
        nights_to=14,
        days_ahead=14,
        price_max=Config.MAX_PRICE,
    )

    if not tours:
        print("❌ Туры не найдены. Попробуй увеличить MAX_PRICE в .env")
        return

    # Берём первый (самый дешёвый) тур
    tour = tours[0]
    print(f"\n✅ Тур найден: {tour.hotel_name}, {tour.country}")

    # ── Вариант 1: с Claude API ───────────────────────────────
    if Config.ANTHROPIC_API_KEY:
        print("\n📝 Генерирую текст через Claude AI...")
        try:
            post_ai = generate_post(tour, Config.ANTHROPIC_API_KEY)
            print("\n" + "=" * 60)
            print("  ГОТОВЫЙ ПОСТ (с ИИ):")
            print("=" * 60)
            print(post_ai)
            print("=" * 60)
            print("\n✅ Шаг 2 пройден! ИИ генерирует тексты.")
        except Exception as e:
            print(f"⚠️  Ошибка Claude API: {e}")
            print("Показываю шаблонный вариант...")
            _show_template_post(tour)
    else:
        print("\n⚠️  ANTHROPIC_API_KEY не задан — показываю шаблонный вариант")
        print("(добавь ключ в .env чтобы получать живые тексты от ИИ)")
        _show_template_post(tour)

    print("\nСледующий шаг: python test_step3.py")
    print("(публикация поста в Telegram)")


def _show_template_post(tour):
    post = generate_post_without_ai(tour)
    print("\n" + "=" * 60)
    print("  ГОТОВЫЙ ПОСТ (шаблон без ИИ):")
    print("=" * 60)
    print(post)
    print("=" * 60)


if __name__ == "__main__":
    main()
