"""
test_step1.py — Проверка подключения к Tourvisor API

Запусти: python test_step1.py
Ожидаемый результат: список горящих туров в консоли

Если видишь ошибку — проверь .env файл (логин и пароль Tourvisor).
"""

from config import Config
from tourvisor.client import TourvisorClient


def main():
    print("=" * 60)
    print("  ШАГИ 1: Проверка Tourvisor API")
    print("=" * 60)

    # Создаём клиент с твоими логином и паролем из .env
    client = TourvisorClient(
        login=Config.TOURVISOR_LOGIN,
        password=Config.TOURVISOR_PASSWORD,
    )

    # ── Тест 1: получаем список городов вылета ────────────────
    print("\n📍 Тест 1: Список городов вылета")
    print("-" * 40)
    cities = client.get_departure_cities()

    if not cities:
        print("❌ Города не получены. Проверь логин/пароль в .env")
        return

    print(f"✅ Получено городов: {len(cities)}")
    print("\nПервые 10 городов:")
    for city in cities[:10]:
        print(f"   ID={city.get('id', '?'):>5}  {city.get('name', '?')}")

    # ── Тест 2: ищем ID города Уфа ───────────────────────────
    print("\n📍 Тест 2: Ищем код города Уфа")
    print("-" * 40)
    ufa_id = client.find_city_id("Уфа")

    if ufa_id:
        print(f"✅ Уфа найдена! ID = {ufa_id}")
    else:
        print("⚠️  Уфа не найдена. Печатаем все города:")
        for city in cities:
            print(f"   ID={city.get('id', '?'):>5}  {city.get('name', '?')}")
        print("\nНайди Уфу в списке и запиши её ID в config.py")
        return

    # ── Тест 3: получаем список стран ─────────────────────────
    print("\n📍 Тест 3: Список стран назначения")
    print("-" * 40)
    countries = client.get_countries()
    print(f"✅ Получено стран: {len(countries)}")
    print("Популярные направления:")
    popular = ["Турция", "Египет", "ОАЭ", "Таиланд", "Мальдивы", "Куба"]
    for country in countries:
        if country.get("name", "") in popular:
            print(f"   ID={country.get('id', '?'):>5}  {country.get('name', '?')}")

    # ── Тест 4: ищем горящие туры ─────────────────────────────
    print(f"\n📍 Тест 4: Поиск горящих туров из Уфы (ID={ufa_id})")
    print("-" * 40)
    print("Ищем: вылет в ближайшие 14 дней, 7-14 ночей, до 150 000₽")
    print("(это может занять 30-60 секунд — Tourvisor ищет в реальном времени)\n")

    tours = client.find_hot_tours(
        departure_id=ufa_id,
        nights_from=Config.NIGHTS_FROM,
        nights_to=Config.NIGHTS_TO,
        days_ahead=Config.DAYS_AHEAD,
        price_max=Config.MAX_PRICE,
    )

    # ── Показываем результаты ──────────────────────────────────
    print("\n" + "=" * 60)
    if not tours:
        print("⚠️  Туры не найдены.")
        print("Попробуй:")
        print("  1. Увеличь MAX_PRICE в .env (например до 200000)")
        print("  2. Увеличь DAYS_AHEAD в .env (например до 30)")
        print("  3. Убедись что логин/пароль Tourvisor правильные")
    else:
        print(f"✅ НАЙДЕНО ТУРОВ: {len(tours)}")
        print("\nТоп-5 самых дешёвых:")
        print("=" * 60)
        for i, tour in enumerate(tours[:5], 1):
            print(f"\n  [{i}] {tour}")
            print()

        print("=" * 60)
        print("🎉 Шаг 1 пройден! Tourvisor API работает.")
        print("Следующий шаг: python test_step2.py")
        print("(генерация текста поста через ИИ)")

    # Сохраняем ID Уфы для следующих шагов
    print(f"\n💾 Запиши в .env: DEPARTURE_CITY_ID={ufa_id}")


if __name__ == "__main__":
    main()
