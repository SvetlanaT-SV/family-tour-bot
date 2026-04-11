"""
test_sheets.py — Проверка новой архитектуры Google Sheets → публикация

Запусти: python test_sheets.py

Что проверяет:
  1. Подключение к Google Sheets
  2. Создание листа "Туры к публикации" с заголовками
  3. Добавление тестового тура
  4. Генерацию поста из данных тура
  5. (опционально) Публикацию в Telegram

Перед запуском:
  - Заполни GOOGLE_CREDENTIALS_FILE и GOOGLE_SHEET_ID в .env
  - Убедись что google_credentials.json лежит в папке проекта
"""

from config import Config
from sheets.client import SheetsClient
from ai.generator import generate_post_from_dict

print("=" * 60)
print("  ТЕСТ: Google Sheets → публикация")
print("=" * 60)

# ── Тест 1: подключение к Sheets ─────────────────────────────
print("\n📋 Тест 1: Подключение к Google Sheets")
print("-" * 40)

if not Config.GOOGLE_CREDENTIALS_FILE or not Config.GOOGLE_SHEET_ID:
    print("❌ Не заполнены GOOGLE_CREDENTIALS_FILE или GOOGLE_SHEET_ID в .env")
    print("   Пропускаю тесты Sheets — проверяю только генерацию поста")
    sheets = None
else:
    sheets = SheetsClient(Config.GOOGLE_CREDENTIALS_FILE, Config.GOOGLE_SHEET_ID)
    if sheets.gc:
        print("✅ Подключение успешно!")
        print("   Листы созданы автоматически если их не было")
    else:
        print("❌ Не удалось подключиться к Sheets")
        sheets = None

# ── Тест 2: добавление тестового тура ────────────────────────
TEST_TOUR = {
    "Страна":       "Турция",
    "Курорт":       "Анталья",
    "Отель":        "Rixos Premium Belek",
    "Звёзды":       "5",
    "Питание":      "Ultra All Inclusive",
    "Дата вылета":  "20.04.2026",
    "Ночей":        "7",
    "Цена/чел":     "45000",
    "Фото URL":     "",
    "Ссылка":       "",
}

if sheets:
    print("\n📋 Тест 2: Добавление тестового тура в Sheets")
    print("-" * 40)
    print("Тур:", TEST_TOUR)
    try:
        ss = sheets._get_spreadsheet()
        ws = ss.worksheet("Туры к публикации")
        row = ["НОВЫЙ"] + [TEST_TOUR.get(h, "") for h in [
            "Страна", "Курорт", "Отель", "Звёзды", "Питание",
            "Дата вылета", "Ночей", "Цена/чел", "Фото URL", "Ссылка"
        ]] + ["", ""]
        ws.append_row(row)
        print("✅ Тестовый тур добавлен в таблицу!")
        print("   Открой Google Sheets — там должна быть строка со статусом НОВЫЙ")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

# ── Тест 3: генерация поста ───────────────────────────────────
print("\n📋 Тест 3: Генерация поста из данных тура")
print("-" * 40)

if Config.ANTHROPIC_API_KEY:
    print("Claude API ключ найден — генерирую с ИИ...")
    post = generate_post_from_dict(TEST_TOUR, Config.ANTHROPIC_API_KEY)
else:
    print("Claude API ключ не найден — генерирую по шаблону...")
    post = generate_post_from_dict(TEST_TOUR)

print("\n" + "=" * 60)
print("СГЕНЕРИРОВАННЫЙ ПОСТ:")
print("=" * 60)
print(post)
print("=" * 60)

# ── Тест 4: чтение туров из Sheets ───────────────────────────
if sheets:
    print("\n📋 Тест 4: Чтение туров со статусом НОВЫЙ")
    print("-" * 40)
    pending = sheets.get_pending_tours()
    if pending:
        print(f"✅ Найдено туров к публикации: {len(pending)}")
        for t in pending:
            print(f"   Строка {t['_row_number']}: {t.get('Отель')} / {t.get('Страна')}")
    else:
        print("⚠️ Туров со статусом НОВЫЙ не найдено")
        print("   Добавь тур в таблицу и поставь статус НОВЫЙ")

print("\n" + "=" * 60)
print("🎉 Тест завершён!")
print("\nСледующий шаг:")
print("  1. Открой Google Sheets → лист 'Туры к публикации'")
print("  2. Убедись что тестовый тур там есть со статусом НОВЫЙ")
print("  3. Запусти python main.py — бот найдёт его и опубликует")
print("=" * 60)
