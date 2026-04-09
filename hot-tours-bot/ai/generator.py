"""
ai/generator.py — Генератор текста поста через Claude API

Берёт сухие данные тура (отель, цена, питание...)
и превращает их в живой, привлекательный пост для ВК/Telegram.

Claude Haiku — самая дешёвая модель, стоит ~0.1₽ за пост.
"""

import anthropic
from tourvisor.client import Tour


# Шаблон поста — Claude заполняет [ОПИСАНИЕ] и [ПРЕИМУЩЕСТВА]
POST_TEMPLATE = """🔥 ГОРЯЩИЙ ТУР! {country}

✈️ Вылет: {date_from} из {city_from} ({nights} {nights_word})
🏨 {hotel_name} {stars}
🍽 Питание: {meal}
{sea_line}
💰 Цена: {price_per_person}/чел

{ai_description}

{ai_advantages}

📩 Хотите забронировать? Напишите нам!
⚡ Количество мест ограничено!

#горящийтур #{country_tag} #турагентствоУфа #FamilyTour"""


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
        meal             = tour.meal_label,
        sea_line         = sea_line,
        price_per_person = tour.formatted_price_per_person,
        ai_description   = ai_description,
        ai_advantages    = ai_advantages,
        country_tag      = _country_tag(tour.country),
    )

    return post


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
        meal             = tour.meal_label,
        sea_line         = sea_line,
        price_per_person = tour.formatted_price_per_person,
        ai_description   = f"Отличный отдых в {tour.country} ждёт вас!",
        ai_advantages    = "\n".join(advantages),
        country_tag      = _country_tag(tour.country),
    )
