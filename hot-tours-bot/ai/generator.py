"""
ai/generator.py — Генератор текста поста через ИИ.

Приоритет: GigaChat (если задан GIGACHAT_AUTH_KEY) → Claude (если ANTHROPIC_API_KEY)
→ шаблонный fallback (всегда работает).
"""

import anthropic
import logging
import os
import random
import re
from tourvisor.client import Tour

logger = logging.getLogger(__name__)


# Шаблон поста — Claude заполняет [ОПИСАНИЕ] и [ПРЕИМУЩЕСТВА]
POST_TEMPLATE = """<b>🔥 ГОРЯЩИЙ ТУР! {country}</b>

✈️ Вылет: {date_from} из {city_from} ({nights} {nights_word})
🏨 <b>{hotel_name}</b> {stars}
🍽 Питание: {meal}
{sea_line}
💰 Цена: <b>{price_per_person}/чел</b>

{ai_description}

{ai_advantages}

📩 Написать нам: <b>@hottourpegas_bot</b>
📞 Позвонить: <b>+7 (917) 044-21-00</b>
⚡ Количество мест ограничено!

#горящийтур #{country_tag}"""


# Перевод кодов питания на русский язык
MEAL_RU = {
    "AI":                   "Всё включено",
    "ALL":                  "Всё включено",
    "All Inclusive":        "Всё включено",
    "UAI":                  "Ультра всё включено",
    "Ultra All Inclusive":  "Ультра всё включено",
    "HB":                   "Полупансион",
    "Half Board":           "Полупансион",
    "FB":                   "Полный пансион",
    "Full Board":           "Полный пансион",
    "BB":                   "Завтрак",
    "Bed & Breakfast":      "Завтрак",
    "Bed and Breakfast":    "Завтрак",
    "RO":                   "Без питания",
    "Room Only":            "Без питания",
    "SC":                   "Без питания",
}


def _meal_ru(meal) -> str:
    """Возвращает русское название типа питания, если код известен."""
    meal = str(meal).strip()
    return MEAL_RU.get(meal, meal)


def _nights_word(n: int) -> str:
    """Склонение слова 'ночь': 7 ночей, 1 ночь, 3 ночи"""
    if 11 <= n % 100 <= 14:
        return "ночей"
    r = n % 10
    if r == 1:
        return "ночь"
    if 2 <= r <= 4:
        return "ночи"
    return "ночей"


def _country_tag(country: str) -> str:
    """Превращает название страны в хэштег: 'ОАЭ' → 'ОАЭ'"""
    return country.replace(" ", "_")


# Для каждой страны: (куда? — "в Турцию"/"на Мальдивы",  где? — "в Турции"/"на Мальдивах")
# Некоторые страны требуют предлог "на" вместо "в": острова, некоторые края.
COUNTRY_PREPS = {
    # страна:          (куда,             где)
    "Россия":          ("в Россию",        "в России"),
    "Турция":          ("в Турцию",        "в Турции"),
    "Египет":          ("в Египет",        "в Египте"),
    "ОАЭ":             ("в ОАЭ",           "в ОАЭ"),
    "Таиланд":         ("в Таиланд",       "в Таиланде"),
    "Мальдивы":        ("на Мальдивы",     "на Мальдивах"),
    "Куба":            ("на Кубу",         "на Кубе"),
    "Доминикана":      ("в Доминикану",    "в Доминикане"),
    "Индия":           ("в Индию",         "в Индии"),
    "Шри-Ланка":       ("на Шри-Ланку",    "на Шри-Ланке"),
    "Индонезия":       ("в Индонезию",     "в Индонезии"),
    "Бали":            ("на Бали",         "на Бали"),
    "Вьетнам":         ("во Вьетнам",      "во Вьетнаме"),
    "Грузия":          ("в Грузию",        "в Грузии"),
    "Абхазия":         ("в Абхазию",       "в Абхазии"),
    "Беларусь":        ("в Беларусь",      "в Беларуси"),
    "Армения":         ("в Армению",       "в Армении"),
    "Азербайджан":     ("в Азербайджан",   "в Азербайджане"),
    "Узбекистан":      ("в Узбекистан",    "в Узбекистане"),
    "Казахстан":       ("в Казахстан",     "в Казахстане"),
    "Киргизия":        ("в Киргизию",      "в Киргизии"),
    "Марокко":         ("в Марокко",       "в Марокко"),
    "Тунис":           ("в Тунис",         "в Тунисе"),
    "Иордания":        ("в Иорданию",      "в Иордании"),
    "Израиль":         ("в Израиль",       "в Израиле"),
    "Кипр":            ("на Кипр",         "на Кипре"),
    "Греция":          ("в Грецию",        "в Греции"),
    "Италия":          ("в Италию",        "в Италии"),
    "Испания":         ("в Испанию",       "в Испании"),
    "Черногория":      ("в Черногорию",    "в Черногории"),
    "Хорватия":        ("в Хорватию",      "в Хорватии"),
    "Болгария":        ("в Болгарию",      "в Болгарии"),
    "Сербия":          ("в Сербию",        "в Сербии"),
    "Венгрия":         ("в Венгрию",       "в Венгрии"),
    "Чехия":           ("в Чехию",         "в Чехии"),
    "Австрия":         ("в Австрию",       "в Австрии"),
    "Франция":         ("во Францию",      "во Франции"),
    "Португалия":      ("в Португалию",    "в Португалии"),
    "Малайзия":        ("в Малайзию",      "в Малайзии"),
    "Сингапур":        ("в Сингапур",      "в Сингапуре"),
    "Филиппины":       ("на Филиппины",    "на Филиппинах"),
    "Сейшелы":         ("на Сейшелы",      "на Сейшелах"),
    "Сейшельские острова": ("на Сейшелы", "на Сейшелах"),
    "Маврикий":        ("на Маврикий",     "на Маврикии"),
    "Танзания":        ("в Танзанию",      "в Танзании"),
    "Занзибар":        ("на Занзибар",     "на Занзибаре"),
    "Кения":           ("в Кению",         "в Кении"),
    "ЮАР":             ("в ЮАР",           "в ЮАР"),
    "Мексика":         ("в Мексику",       "в Мексике"),
    "Ямайка":          ("на Ямайку",       "на Ямайке"),
    "Бразилия":        ("в Бразилию",      "в Бразилии"),
    "Аргентина":       ("в Аргентину",     "в Аргентине"),
    "Перу":            ("в Перу",          "в Перу"),
    "Китай":           ("в Китай",         "в Китае"),
    "Япония":          ("в Японию",        "в Японии"),
    "Южная Корея":     ("в Южную Корею",   "в Южной Корее"),
    "Камбоджа":        ("в Камбоджу",      "в Камбодже"),
    "Лаос":            ("в Лаос",          "в Лаосе"),
    "Мьянма":          ("в Мьянму",        "в Мьянме"),
    "Непал":           ("в Непал",         "в Непале"),
    "Бутан":           ("в Бутан",         "в Бутане"),
}


def _country_to(country: str) -> str:
    """Фраза 'куда?' — 'в Турцию' / 'на Мальдивы'. Если нет в словаре — 'в {country}'."""
    pair = COUNTRY_PREPS.get(country.strip())
    return pair[0] if pair else f"в {country}"


def _country_in(country: str) -> str:
    """Фраза 'где?' — 'в Турции' / 'на Мальдивах'. Если нет в словаре — 'в {country}'."""
    pair = COUNTRY_PREPS.get(country.strip())
    return pair[1] if pair else f"в {country}"


def _country_acc(country: str) -> str:
    """Совместимость: возвращает слово в винительном ('Турцию' / 'Мальдивы')."""
    phrase = _country_to(country)
    # отрезаем предлог
    for p in ("во ", "на ", "в "):
        if phrase.startswith(p):
            return phrase[len(p):]
    return phrase


# Telegram parse_mode=HTML принимает только эти теги:
_TG_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
                     "a", "code", "pre", "blockquote", "tg-spoiler", "span"}


def _sanitize_html_for_telegram(text: str) -> str:
    """Чистит HTML от тегов которые Telegram не поддерживает (br, p, div и т.п.)."""
    if not text:
        return text
    # Markdown → HTML: **жирный** → <b>жирный</b>, *курсив* → <i>курсив</i>
    text = re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", text)
    # Заменяем переводы строк-теги на \n
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</\s*p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*p[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?\s*div[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?\s*hr[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Убираем все остальные теги, кроме разрешённых
    def _strip(match):
        tag = match.group(1).lower()
        if tag in _TG_ALLOWED_TAGS:
            return match.group(0)
        return ""
    text = re.sub(r"</?\s*([a-zA-Z][a-zA-Z0-9-]*)[^>]*>", _strip, text)
    # Убираем тройные и более переводов строк
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Родительный падеж городов (откуда? — из Уфы, из Москвы)
CITY_GENITIVE = {
    "Уфа":            "Уфы",
    "Москва":         "Москвы",
    "Санкт-Петербург":"Санкт-Петербурга",
    "Петербург":      "Петербурга",
    "Казань":         "Казани",
    "Екатеринбург":   "Екатеринбурга",
    "Новосибирск":    "Новосибирска",
    "Челябинск":      "Челябинска",
    "Самара":         "Самары",
    "Пермь":          "Перми",
    "Нижний Новгород":"Нижнего Новгорода",
    "Краснодар":      "Краснодара",
    "Ростов-на-Дону": "Ростова-на-Дону",
    "Волгоград":      "Волгограда",
    "Сочи":           "Сочи",
    "Минеральные Воды":"Минеральных Вод",
    "Минводы":        "Минеральных Вод",
    "Тюмень":         "Тюмени",
    "Оренбург":       "Оренбурга",
    "Ижевск":         "Ижевска",
    "Магнитогорск":   "Магнитогорска",
    "Стерлитамак":    "Стерлитамака",
    "Нефтекамск":     "Нефтекамска",
}


def _city_gen(city: str) -> str:
    """Возвращает город в родительном падеже (откуда? — из Уфы)."""
    return CITY_GENITIVE.get(city.strip(), city)


# ── Пулы случайных преимуществ ──────────────────────────────────

_ADV_MEAL_AI = [
    "✅ Всё включено — еда, напитки, развлечения",
    "✅ Система All Inclusive — ни о чём не беспокойтесь",
    "✅ Питание и напитки на весь отдых — включено",
    "✅ Ресторан, бар, перекусы — всё уже оплачено",
    "✅ Можно ни разу не доставать кошелёк — всё включено",
]

_ADV_MEAL_UAI = [
    "✅ Ultra All Inclusive — по-настоящему ни в чём себе не отказывать",
    "✅ Премиум «всё включено» — алкоголь, рестораны à la carte",
    "✅ UAI — фирменные напитки и блюда от шеф-повара включены",
]

_ADV_MEAL_HB = [
    "✅ Полупансион — завтрак и ужин уже в цене",
    "✅ HB — начинайте день с завтрака, заканчивайте ужином",
    "✅ Кормят утром и вечером — днём свобода",
]

_ADV_MEAL_BB = [
    "✅ Завтрак включён — утро без забот",
    "✅ С утра — шведский стол, потом исследуйте город",
    "✅ Завтрак в отеле, днём — местная кухня",
]

_ADV_STARS_5 = [
    "✅ Отель {s}⭐ — премиум сервис и комфорт",
    "✅ {s}⭐ — настоящая роскошь на отдыхе",
    "✅ Люкс-категория {s}⭐ — обслуживание на высшем уровне",
    "✅ {s} звёзд — внимание к каждой мелочи",
]

_ADV_STARS_4 = [
    "✅ Отель {s}⭐ — высокий уровень комфорта",
    "✅ {s}⭐ — проверенное качество для отдыха",
    "✅ {s} звезды — всё необходимое для приятного отпуска",
    "✅ Солидные {s}⭐ — без сюрпризов",
]

_ADV_FROM_CITY = [
    "✅ Вылет прямо из {city_gen} — без пересадок",
    "✅ Рейс из {city_gen} — собрались и поехали",
    "✅ Из аэропорта {city_gen} — удобно и быстро",
    "✅ Прямой рейс из {city_gen} — никаких стыковок",
    "✅ Вылет из {city_gen} — экономите время и силы",
]

_ADV_HOT_PRICE = [
    "✅ Горящая цена — не упустите момент",
    "✅ Цена падает прямо сейчас — успейте забронировать",
    "✅ Такое бывает редко — ловите, пока есть",
    "✅ Горящий тур = серьёзная экономия",
    "✅ Цена ниже обычной — только на горящих местах",
    "✅ Эта цена живёт часы — дальше дороже",
    "✅ Бронирование сейчас — лучшая цена этой недели",
]

_ADV_FAMILY = [
    "✅ Подходит и для пар, и для семей",
    "✅ Хорошо для отдыха вдвоём или компанией",
    "✅ Удобный формат отдыха — без хлопот",
]

_ADV_SEA_CLOSE = [
    "✅ Первая линия — море за минуту пешком",
    "✅ Прямо у моря — не тратьте время на дорогу",
    "✅ До пляжа — пара минут, считайте каждую минуту солнца",
]

_ADV_BONUS = [
    "✅ Проверенный туроператор Pegas Touristik",
    "✅ Сопровождение от менеджера до возвращения",
    "✅ Все документы подготовим за вас",
    "✅ Наш опыт более 12 лет — знаем направления",
    "✅ Подберём подходящий вариант под ваш запрос",
]


def _pick_advantages(meal: str, stars_digit: str, has_children_ok: bool = True,
                     sea_close: bool = False, count: int = 2,
                     city_from: str = "Уфа") -> list[str]:
    """Случайно собирает набор преимуществ без дублей. По умолчанию 2 галочки."""
    city_nom = city_from
    city_gen = _city_gen(city_from)
    from_city_pool = [t.format(city_nom=city_nom, city_gen=city_gen) for t in _ADV_FROM_CITY]
    all_pools = [from_city_pool, _ADV_HOT_PRICE]

    meal_upper = meal.upper().strip() if meal else ""
    if meal_upper in ("UAI", "ULTRA ALL INCLUSIVE"):
        all_pools.append(_ADV_MEAL_UAI)
    elif meal_upper in ("AI", "ALL", "ALL INCLUSIVE"):
        all_pools.append(_ADV_MEAL_AI)
    elif meal_upper in ("HB", "HALF BOARD"):
        all_pools.append(_ADV_MEAL_HB)
    elif meal_upper in ("BB", "BED & BREAKFAST", "BED AND BREAKFAST"):
        all_pools.append(_ADV_MEAL_BB)

    if stars_digit and stars_digit.isdigit():
        s = int(stars_digit)
        if s >= 5:
            all_pools.append([t.format(s=s) for t in _ADV_STARS_5])
        elif s >= 4:
            all_pools.append([t.format(s=s) for t in _ADV_STARS_4])

    if sea_close:
        all_pools.append(_ADV_SEA_CLOSE)

    if has_children_ok:
        all_pools.append(_ADV_FAMILY)

    all_pools.append(_ADV_BONUS)

    # Выбираем count разных пулов и из каждого по одной случайной фразе
    chosen_pools = random.sample(all_pools, k=min(count, len(all_pools)))
    return [random.choice(pool) for pool in chosen_pools]


def _random_headline(country: str, hotel: str, nights: str,
                     price_str: str, meal_ru: str, stars_str: str,
                     city_from: str = "Уфа") -> str:
    """
    Случайный цепляющий заголовок для поста.
    country   — страна в именительном падеже ("Турция")
    nights    — уже склонённая строка ("7 ночей" / "3 ночи")
    city_from — город вылета ("Уфа", "Казань", ...)
    """
    country_to  = _country_to(country)   # "в Турцию" / "на Мальдивы"
    country_in  = _country_in(country)   # "в Турции" / "на Мальдивах"
    country_acc = _country_acc(country)  # "Турцию" / "Мальдивы" (без предлога)
    city_nom    = city_from
    city_gen    = _city_gen(city_from)
    templates = [
        # ── Горячие / срочные ──
        f"🔥 Горящий тур {country_to} — мест почти нет!",
        f"⏰ Забронируйте за 10 минут — {country} уходит!",
        f"🚨 Внимание: {country} по смешной цене!",
        f"⚡ Последние места: {country}, {hotel}",
        f"🔥 Этот тур исчезнет к вечеру — {country} от {price_str}",

        # ── FOMO / страх упустить ──
        f"😱 Такой цены вы ещё не видели — {country}!",
        f"💔 Упустите — пожалеете: {country}, {price_str}/чел",
        f"👀 Пока вы думаете — кто-то бронирует {country_acc}",
        f"🎯 Не для всех: горящая цена на {country_acc}",

        # ── Мечта / эмоции ──
        f"🤩 Мечтали отдохнуть? Вот ваш шанс — {country}!",
        f"💫 {country} зовёт — и не отпустит",
        f"🌊 Море, солнце, {meal_ru} — {country} ждёт!",
        f"🌴 {country} — там, где вы забудете про будни",
        f"✨ Чемодан, паспорт, улыбка — и вы {country_in}",
        f"🌺 {country}: когда отдых превращается в сказку",

        # ── Инсайдерские / эксклюзив ──
        f"🤫 Нашли для вас: {hotel} {country_in}",
        f"💎 Тихая находка: {country}, {hotel} {stars_str}",
        f"🔑 Секретная цена: {country} от {price_str}/чел",
        f"🎁 Подарок дня — тур {country_to}",

        # ── Практические / про деньги ──
        f"💰 {country}: отдых без переплаты",
        f"💸 {country} от {price_str}/чел — считайте выгоду",
        f"🏷 Цена дня: {country}, {nights}, {meal_ru}",
        f"💫 Лучшая цена недели — {country}",
        f"🎯 Готовый вариант: {country}, всё включено (ну почти)",

        # ── Вопросы / обращение ──
        f"🤔 Куда в отпуск? Вот ответ — {country}",
        f"✈️ Улетаем {country_to}? Осталось несколько мест!",
        f"🏖 Надоели серые будни? Летим {country_to}",
        f"🌞 Соскучились по солнцу? {country} через пару дней",

        # ── Локальные / из города вылета ──
        f"✈️ Из {city_gen} {country_to} — прямой вылет, {nights}",
        f"🛫 Прямо из {city_gen} — {country}, {price_str}/чел",
        f"🏡 Без пересадок из {city_gen} — {country} на {nights}",

        # ── Про отель ──
        f"🏨 {hotel} {stars_str} — и это цена?!",
        f"😍 {hotel} {stars_str} {country_in} — сказочно выгодно",
        f"🎯 Нашли: {hotel} {stars_str}, {country}",
        f"💥 {hotel} {country_in} — глаза не верят цене",

        # ── Про питание / атмосферу ──
        f"🍹 {meal_ru}, море, {country} — вот это план",
        f"🥂 {country}: {meal_ru.lower()}, солнце, никаких забот",
        f"🍽 {country}, {meal_ru.lower()} — ни о чём не думать {nights}",

        # ── Интригующие / нестандартные ──
        f"😮 Три цифры цены — и вы {country_in}",
        f"🎉 Есть повод: {country} от {price_str}/чел",
        f"🌟 Пока все смотрят — мы нашли {country_acc}",
        f"👇 Смотрите внимательно: {country}, {nights}",
        f"🍀 Повезло тем, кто первый: {country}",

        # ── Про время ──
        f"📅 Отпуск уже скоро: {country}, вылет {nights}",
        f"⌛ Пока отпуск не сгорел — летим {country_to}",
        f"🚀 Через пару дней — {country}. Вы готовы?",

        # ── Про семью / компанию ──
        f"👨‍👩‍👧 Всей семьёй — {country_to}",
        f"💑 Вдвоём {country_in}: {nights} на двоих",

        # ── Слоганы ──
        f"🌴 {country} — это не мечта, это реальная цена",
        f"🔑 Ваш билет на море: {hotel}, {country}",
        f"🙌 Наконец-то! {country} по цене, которую ждали",
        f"💃 Отдых {country_in} — дешевле чем думаете",
    ]
    return random.choice(templates)


def generate_post(tour: Tour, api_key: str) -> str:
    """
    Генерирует готовый текст поста для одного тура.

    Как работает:
    1. Собираем базовые данные из объекта Tour
    2. Отправляем Claude короткий запрос — он пишет описание и преимущества
    3. Подставляем в шаблон и возвращаем готовый текст

    Параметры:
        tour    — объект Tour с данными о туре
        api_key — ключ Claude API из .env
    """

    # ── Шаг 1: генерируем описание и преимущества через Claude ──
    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""Ты — копирайтер турагентства. Напиши для поста в ВК два блока на русском языке.

Данные отеля:
- Отель: {tour.hotel_name} {tour.hotel_stars}★
- Страна: {tour.country}, {tour.resort}
- Питание: {tour.meal_label}
- Расстояние до моря: {tour.distance_sea} м
- Ночей: {tour.nights}

Блок 1 — ОПИСАНИЕ (1-2 предложения, атмосферное, без воды):
Начни с новой строки, без заголовка. Например: "Роскошный отель первой линии с собственным пляжем..."

Блок 2 — ПРЕИМУЩЕСТВА (3-4 пункта, каждый с эмодзи ✅):
Конкретные факты об отеле. Например:
✅ Первая линия — 50 метров до пляжа
✅ Ultra All Inclusive — питание и напитки включены
✅ Аквапарк и SPA в отеле

Отвечай только двумя блоками, без лишних слов и заголовков."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",  # самая быстрая и дешёвая модель
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )

    ai_text = message.content[0].text.strip()

    # Разделяем на описание и преимущества по строке с ✅
    parts = ai_text.split("✅", 1)
    if len(parts) == 2:
        ai_description = parts[0].strip()
        ai_advantages  = "✅" + parts[1].strip()
    else:
        # Если Claude не разделил как надо — берём весь текст как описание
        ai_description = ai_text
        ai_advantages  = ""

    # ── Шаг 2: подставляем в шаблон ──────────────────────────
    sea_line = f"🌊 До моря: {tour.distance_sea} м" if tour.distance_sea else ""

    post = POST_TEMPLATE.format(
        country          = tour.country,
        date_from        = tour.date_from,
        city_from        = _city_gen(tour.city_from) if tour.city_from else "Уфы",
        nights           = tour.nights,
        nights_word      = _nights_word(tour.nights),
        hotel_name       = tour.hotel_name,
        stars            = tour.stars_str,
        meal             = _meal_ru(tour.meal_label),
        sea_line         = sea_line,
        price_per_person = tour.formatted_price_per_person,
        ai_description   = ai_description,
        ai_advantages    = ai_advantages,
        country_tag      = _country_tag(tour.country),
    )

    return post


def generate_post_from_dict(data: dict, api_key: str = "") -> str:
    """
    Генерирует пост из словаря с данными тура (из Google Sheets).

    data — строка из листа 'Туры к публикации':
        Страна, Курорт, Отель, Звёзды, Питание,
        Дата вылета, Ночей, Цена/чел, Ссылка

    Если api_key не задан — генерирует по шаблону без ИИ.
    """
    country   = str(data.get("Страна", "") or "")
    resort_raw = str(data.get("Курорт", "") or "").strip()
    resort    = resort_raw  # пусто если не заполнено
    hotel     = str(data.get("Отель", "") or "")
    stars     = ""  # столбец Звёзды удалён из таблицы
    meal      = str(data.get("Питание", "") or "")
    date      = str(data.get("Дата вылета", "") or "")
    nights    = str(data.get("Ночей", "") or "")
    price     = str(data.get("Цена/чел", "") or "")
    link      = str(data.get("Ссылка", "") or "")
    city_from = (str(data.get("Город вылета", "") or "").strip() or "Уфа")
    city_gen  = _city_gen(city_from)
    import logging as _lg
    _lg.getLogger(__name__).info(
        f"Генерация поста: страна={country!r}, курорт={data.get('Курорт')!r}, "
        f"город_вылета={city_from!r} (из колонки {data.get('Город вылета')!r}), "
        f"все ключи строки: {list(data.keys())}"
    )

    stars_str  = f"{'⭐' * int(stars)}" if stars.isdigit() else stars
    meal_ru    = _meal_ru(meal)
    nights_str = f"{nights} {_nights_word(int(nights))}" if nights.isdigit() else f"{nights} ночей"
    # Очищаем цену — оставляем только цифры и точку
    price_clean = re.sub(r"[^\d.]", "", price)
    price_str   = f"{int(float(price_clean)):,}".replace(",", " ") + " ₽" if price_clean else "уточняйте"
    link_line  = f"\n🔗 Подробнее: {link}" if link else ""
    headline   = _random_headline(country, hotel, nights_str, price_str, meal_ru, stars_str, city_from=city_from)

    country_to = _country_to(country)   # "в Турцию" / "на Мальдивы"
    country_in = _country_in(country)   # "в Турции" / "на Мальдивах"

    # Особенности отеля — заполняются вручную в Google Sheets
    features = str(data.get("Особенности отеля", "") or "").strip()
    real_hotel_block = ""
    if features:
        real_hotel_block = (
            "\n\nРЕАЛЬНЫЕ ОСОБЕННОСТИ ОТЕЛЯ (от агентства — это правда, можно упоминать):\n"
            f"{features}\n"
        )
        logger.info(f"Особенности отеля переданы в промпт: {features[:80]}")

    # Единый промпт для любого ИИ (Claude / GigaChat)
    ai_prompt = f"""Ты — копирайтер турагентства Pegas Touristik (Уфа, опыт 12+ лет). Напиши продающий пост для Telegram с HTML-разметкой.

Данные тура:
- Страна: {country}
- «Куда?» (куда летим): {country_to}  — используй эту фразу целиком
- «Где?» (где отдыхаем): {country_in}  — используй эту фразу целиком
- Курорт: {resort or '(не указан, используй фразу про страну)'}
- Отель: {hotel} {stars_str}
- Питание: {meal_ru}
- Вылет: {date}, {nights_str}
- Город вылета: {city_from} (в родительном падеже — из {city_gen})
- Цена: от {price_str}/чел

⚠️ КРИТИЧНО про факты:
— ТОЛЬКО факты из «Данные тура» выше {("и из «РЕАЛЬНЫЕ ОСОБЕННОСТИ ОТЕЛЯ» ниже" if real_hotel_block else "")}.
— Если есть «РЕАЛЬНЫЕ ОСОБЕННОСТИ ОТЕЛЯ» — можно и НУЖНО их упоминать, это правда от агентства.
— Если особенностей нет — НЕ упоминай конкретных удобств (SPA, аквапарк, анимация, бассейны, шведский стол, à la carte, фитнес и т.п.). Говори общо.
— Категория звёзд и питание из «Данные тура» — можно использовать.{real_hotel_block}

⚠️ КРИТИЧНО про название отеля:
— Название отеля ПИШИ ТОЧНО как дано: «{hotel}». Не переводи, не транслитерируй, не меняй регистр, не убирай и не добавляй слова. Если оно на английском — оставь на английском. Если с цифрами/символами — сохрани их.
— Так же с названиями курортов: переписывай как написано в «Курорт».

⚠️ КРИТИЧНО про падежи:
— «Куда?» — ТОЛЬКО готовая фраза: {country_to}. Пример: «летим {country_to}», «тур {country_to}».
— «Где?» — ТОЛЬКО готовая фраза: {country_in}. Пример: «отдых {country_in}», «отель {country_in}».
— НЕ пиши «в {country}» — используй готовые фразы выше.
— «Из» всегда с родительным: из {city_gen} (НЕ «из {city_from}»).

Структура поста (строго, в таком порядке):
1. Первая строка — ОРИГИНАЛЬНЫЙ цепляющий заголовок в <b>...</b> с эмодзи. Без штампов «🔥 ГОРЯЩИЙ ТУР», «Горячее предложение». Обращение на «вы», не «ты».
2. Блок деталей строкой за строкой:
   ✈️ Вылет: {date} из {city_gen} ({nights_str})
   🏨 <b>{hotel}</b> {stars_str}
   🍽 Питание: {meal_ru}
   💰 Цена: от <b>{price_str}/чел</b>
3. 1-2 коротких предложения общего настроения (про море, отпуск, страну) — БЕЗ конкретных удобств отеля, которых нет в данных. НЕ описывай интерьеры, бассейны, пляж, услуги, которых не указано.
4. Ровно 2 преимущества с ✅, разные между постами (не банальные).
5. Две строки подряд:
   📩 Написать нам: <b>@hottourpegas_bot</b>
   📞 Позвонить: <b>+7 (917) 044-21-00</b>
6. ⚡ Количество мест ограничено!
7. Хэштеги: #горящийтур #{_country_tag(country)}

Стиль: живой, дружелюбный, как от знакомого. Без воды. Проверь грамматику и склонение.
HTML: разрешён ТОЛЬКО тег <b>...</b>. НЕ используй <br>, <p>, <div>, <hr>, переводы строк делай настоящими переносами (Enter), не тегами."""

    # 1) Пробуем GigaChat (если ключ задан)
    if os.getenv("GIGACHAT_AUTH_KEY", "").strip():
        try:
            from ai.gigachat import generate as giga_generate
            logger.info("Пробую сгенерировать пост через GigaChat...")
            post = giga_generate(ai_prompt, max_tokens=700)
            if post:
                logger.info(f"GigaChat вернул пост ({len(post)} символов)")
                post = _sanitize_html_for_telegram(post)
                if link_line:
                    post += link_line
                return post
            else:
                logger.warning("GigaChat вернул пустой ответ, fallback на шаблон")
        except Exception as e:
            logger.warning(f"GigaChat недоступен, fallback: {e!r}")
    else:
        logger.info("GIGACHAT_AUTH_KEY не задан в окружении")

    # 2) Пробуем Claude (если ключ задан)
    if api_key:
      try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": ai_prompt}],
        )
        post = msg.content[0].text.strip()
        post = _sanitize_html_for_telegram(post)
        if link_line:
            post += link_line
        return post
      except Exception as e:
        logger.warning(f"Claude недоступен, fallback на шаблон: {e}")

    # Шаблонный вариант без ИИ — случайные описания (2-3 фразы)
    country_acc = _country_acc(country)
    country_in  = _country_in(country)   # "в Турции" / "на Мальдивах"
    # loc — фраза "где?" без зависимости от склонения пользовательского ввода:
    # если курорт заполнен — "на курорте Анталья" (апозиция с нейтральной связкой),
    # иначе — готовая фраза по стране ("на Мальдивах" / "в Турции").
    loc = f"на курорте {resort}" if resort else country_in
    # для заголовочных подстановок — просто имя (именительный): резорт или страна
    place = resort if resort else country

    # Описания только из категории «звёздность» — без выдуманных деталей
    # (никакого SPA, бассейнов, аквапарка, шведского стола — этого может не быть в конкретном отеле)
    desc_5_pool = [
        f"Премиум-отель {loc}. Высокий уровень сервиса и внимание к деталям — то, ради чего стоит выбирать пятёрку.",
        f"Отель премиум-класса {loc}. Один из тех вариантов, после которых сложно вернуться к четвёркам.",
        f"Роскошный отдых {loc}. Подойдёт тем, кто хочет получить максимум от отпуска.",
        f"Пять звёзд {loc} — настоящий курортный отдых без компромиссов.",
        f"Премиум-категория {loc}. Когда отдых должен быть на уровне.",
    ]
    desc_4_pool = [
        f"Отличный вариант {loc} с хорошим уровнем сервиса. Проверенный отель для спокойного отдыха.",
        f"Четвёрка {loc} — комфорт без переплаты. Хороший баланс цены и качества.",
        f"Уютный отель {loc}. Всё необходимое для приятного отпуска уже есть.",
        f"Проверенный отдых {loc}. Тот формат, куда возвращаются снова.",
        f"Четыре звезды {loc} — надёжный выбор без сюрпризов.",
    ]
    desc_default_pool = [
        f"Бюджетный отдых {loc} — отдохнуть и не разориться.",
        f"{place} по приятной цене. Простой и удобный вариант для отпуска.",
        f"Недорогая поездка {loc}. Главное впереди — солнце и море.",
        f"Доступный отдых {loc} — для тех, кто умеет ценить отпуск, а не звёзды на вывеске.",
    ]
    if stars.isdigit() and int(stars) >= 5:
        description = random.choice(desc_5_pool)
    elif stars.isdigit() and int(stars) >= 4:
        description = random.choice(desc_4_pool)
    else:
        description = random.choice(desc_default_pool)

    # Особенности отеля из таблицы — добавляем второй фразой к описанию
    if features:
        description = f"{description}\n\n{features}"

    advantages = _pick_advantages(meal=meal, stars_digit=stars, city_from=city_from)
    advantages_str = "\n".join(advantages)

    return f"""<b>{headline}</b>

✈️ Вылет: {date} из {city_gen} ({nights_str})
🏨 <b>{hotel}</b> {stars_str}
🍽 Питание: {meal_ru}
💰 Цена: от <b>{price_str}/чел</b>

{description}

{advantages_str}{link_line}

📩 Написать нам: <b>@hottourpegas_bot</b>
📞 Позвонить: <b>+7 (917) 044-21-00</b>
⚡ Количество мест ограничено!

#горящийтур #{_country_tag(country)}"""


def generate_post_without_ai(tour: Tour) -> str:
    """
    Генерирует пост БЕЗ Claude API — только по шаблону.
    Используется как запасной вариант если нет API-ключа.
    """
    sea_line = f"🌊 До моря: {tour.distance_sea} м" if tour.distance_sea else ""
    country_in = _country_in(tour.country)  # "в Турции" / "на Мальдивах"

    # Без выдуманных деталей про конкретный отель
    desc_pool = [
        f"Отличный отдых {country_in} ждёт вас. Забирайте чемодан — и в путь.",
        f"{tour.country} — солнце, море и настоящий отпуск.",
        f"Пора в отпуск: {tour.country} встречает уже совсем скоро.",
        f"Тёплое море и ласковое солнце — всё это {tour.country}.",
        f"{tour.country}: отдохнуть так, чтобы хватило впечатлений надолго.",
    ]
    description = random.choice(desc_pool)

    advantages = _pick_advantages(
        meal=tour.meal,
        stars_digit=str(tour.hotel_stars) if tour.hotel_stars else "",
        sea_close=bool(tour.distance_sea and tour.distance_sea <= 100),
        city_from=tour.city_from or "Уфа",
    )

    return POST_TEMPLATE.format(
        country          = tour.country,
        date_from        = tour.date_from,
        city_from        = _city_gen(tour.city_from) if tour.city_from else "Уфы",
        nights           = tour.nights,
        nights_word      = _nights_word(tour.nights),
        hotel_name       = tour.hotel_name,
        stars            = tour.stars_str,
        meal             = _meal_ru(tour.meal_label),
        sea_line         = sea_line,
        price_per_person = tour.formatted_price_per_person,
        ai_description   = description,
        ai_advantages    = "\n".join(advantages),
        country_tag      = _country_tag(tour.country),
    )
