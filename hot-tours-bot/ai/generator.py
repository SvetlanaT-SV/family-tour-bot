"""
ai/generator.py — Генератор текста поста через Claude API

Берёт сухие данные тура (отель, цена, питание...)
и превращает их в живой, привлекательный пост для ВК/Telegram.

Claude Haiku — самая дешёвая модель, стоит ~0.1₽ за пост.
"""

import anthropic
import random
import re
from tourvisor.client import Tour


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


def _random_headline(country: str, hotel: str, nights: str,
                     price_str: str, meal_ru: str, stars_str: str) -> str:
    """
    Случайный цепляющий заголовок для поста.
    Каждый раз другой — чтобы лента не выглядела однотипной.
    """
    templates = [
        f"🔥 Горящий тур в {country} — мест почти нет!",
        f"✈️ Улетаем в {country}? Осталось несколько мест!",
        f"🌴 {country} по горящей цене — успей забронировать!",
        f"💥 {hotel} в {country} — вот это цена!",
        f"🤩 Мечтала об {country}? Вот твой шанс!",
        f"⚡ {nights} ночей в {country} — и это реально!",
        f"🏖 {country}: отдых мечты за разумные деньги",
        f"🌊 Море, солнце, {meal_ru} — {country} ждёт!",
        f"😍 {hotel} {stars_str} в {country} — по цене, которую ты не ожидала",
        f"🎯 Идеальный вариант найден: {country}, {hotel}",
        f"🚀 Собирай чемодан! Горящий тур в {country}",
        f"💫 Лучшая цена этой недели — {country} от {price_str}/чел",
        f"🌟 {country}: {nights} ночей {meal_ru.lower()} — по такой цене надолго не задержится",
        f"🏝 Хочешь в отпуск? {country} — это реально!",
        f"🔑 Твой билет на море: {hotel}, {country}",
        f"👀 Смотри, что мы нашли: {country}, {hotel} {stars_str}",
        f"💃 Отдых в {country} — дешевле чем думаешь!",
        f"🙌 Наконец-то! {country} по цене, которую ждала",
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
        city_from        = tour.city_from,
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
    country  = str(data.get("Страна", "") or "")
    resort   = str(data.get("Курорт", "") or "")
    hotel    = str(data.get("Отель", "") or "")
    stars    = ""  # столбец Звёзды удалён из таблицы
    meal     = str(data.get("Питание", "") or "")
    date     = str(data.get("Дата вылета", "") or "")
    nights   = str(data.get("Ночей", "") or "")
    price    = str(data.get("Цена/чел", "") or "")
    link     = str(data.get("Ссылка", "") or "")

    stars_str  = f"{'⭐' * int(stars)}" if stars.isdigit() else stars
    meal_ru    = _meal_ru(meal)
    nights_str = f"{nights} {_nights_word(int(nights))}" if nights.isdigit() else f"{nights} ночей"
    # Очищаем цену — оставляем только цифры и точку
    price_clean = re.sub(r"[^\d.]", "", price)
    price_str   = f"{int(float(price_clean)):,}".replace(",", " ") + " ₽" if price_clean else "уточняйте"
    link_line  = f"\n🔗 Подробнее: {link}" if link else ""
    headline   = _random_headline(country, hotel, nights_str, price_str, meal_ru, stars_str)

    if api_key:
      try:
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""Ты — копирайтер турагентства «Family Tour» (Уфа). Напиши продающий пост для Telegram с HTML-разметкой.

Данные тура:
- Страна/курорт: {country}, {resort}
- Отель: {hotel} {stars_str}
- Питание: {meal_ru}
- Вылет: {date}, {nights_str}
- Цена: от {price_str}/чел

Структура поста (строго):
1. Первая строка — цепляющий заголовок в тегах <b>...</b>. Он должен быть уникальным, вызывать желание читать дальше и купить тур. НЕ используй шаблонное «🔥 ГОРЯЩИЙ ТУР!» — придумай что-то живое. Например: «😍 Мечтала о Турции? Вот твой шанс!» или «⚡ {hotel} — и это горящая цена!» или «🌊 Море, солнце, {meal_ru} — {country} ждёт!»
2. Блок деталей: ✈️ вылет, 🏨 <b>название отеля</b>, 🍽 питание по-русски, 💰 <b>цена</b>
3. 1-2 атмосферных предложения об отдыхе
4. 3-4 преимущества с ✅
5. Строка: 📩 Написать нам: <b>@hottourpegas_bot</b>
6. ⚡ Количество мест ограничено!
7. Хэштеги: #горящийтур #{_country_tag(country)}

Стиль: живой, дружелюбный, как советует подруга. Без воды. Используй только HTML-теги <b> для жирного."""

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        post = msg.content[0].text.strip()
        if link_line:
            post += link_line
        return post
      except Exception:
        pass  # Если API недоступен — используем шаблон

    # Шаблонный вариант без ИИ
    if stars.isdigit() and int(stars) >= 5:
        description = f"Роскошный отель премиум-класса в самом сердце {resort}. Идеально для тех, кто ценит высокий сервис и комфорт."
    elif stars.isdigit() and int(stars) >= 4:
        description = f"Отличный отель в {resort} с высоким уровнем сервиса. Прекрасный выбор для семейного отдыха и пар."
    else:
        description = f"Комфортный отдых в {resort} по отличной цене. Всё необходимое для незабываемого путешествия."

    # Преимущества — без дублей
    advantages = []
    if meal in ("AI", "UAI", "Ultra All Inclusive", "All Inclusive", "Ultra All inclusive"):
        advantages.append("✅ Всё включено — еда, напитки, развлечения")
    elif meal in ("HB", "Half Board"):
        advantages.append("✅ Полупансион — завтрак и ужин включены")
    elif meal in ("BB", "Bed & Breakfast", "Bed and Breakfast"):
        advantages.append("✅ Завтрак включён")

    if stars.isdigit() and int(stars) >= 5:
        advantages.append(f"✅ Отель {stars}⭐ — премиум уровень сервиса")
    elif stars.isdigit() and int(stars) >= 4:
        advantages.append(f"✅ Отель {stars}⭐ — высокий уровень комфорта")

    advantages.append("✅ Вылет из Уфы — не нужно добираться до Москвы")
    advantages.append("✅ Горящая цена — успей забронировать!")

    advantages_str = "\n".join(advantages)

    return f"""<b>{headline}</b>

✈️ Вылет: {date} из Уфы ({nights_str})
🏨 <b>{hotel}</b> {stars_str}
🍽 Питание: {meal_ru}
💰 Цена: от <b>{price_str}/чел</b>

{description}

{advantages_str}{link_line}

📩 Написать нам: <b>@hottourpegas_bot</b>
⚡ Количество мест ограничено!

#горящийтур #{_country_tag(country)}"""


def generate_post_without_ai(tour: Tour) -> str:
    """
    Генерирует пост БЕЗ Claude API — только по шаблону.
    Используется как запасной вариант если нет API-ключа.
    """
    sea_line = f"🌊 До моря: {tour.distance_sea} м" if tour.distance_sea else ""

    advantages = [f"✅ Питание: {tour.meal_label}"]
    if tour.hotel_stars >= 4:
        advantages.append(f"✅ Отель {tour.hotel_stars}★ — высокий уровень сервиса")
    if tour.distance_sea and tour.distance_sea <= 100:
        advantages.append(f"✅ Первая линия — {tour.distance_sea} м до пляжа")
    if tour.children == 0:
        advantages.append("✅ Подходит для взрослых и семей с детьми")

    return POST_TEMPLATE.format(
        country          = tour.country,
        date_from        = tour.date_from,
        city_from        = tour.city_from,
        nights           = tour.nights,
        nights_word      = _nights_word(tour.nights),
        hotel_name       = tour.hotel_name,
        stars            = tour.stars_str,
        meal             = _meal_ru(tour.meal_label),
        sea_line         = sea_line,
        price_per_person = tour.formatted_price_per_person,
        ai_description   = f"Отличный отдых в {tour.country} ждёт вас!",
        ai_advantages    = "\n".join(advantages),
        country_tag      = _country_tag(tour.country),
    )
