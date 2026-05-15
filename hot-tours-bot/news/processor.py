"""
news/processor.py — отбор и переписывание новостей через GigaChat.

Получает на вход список постов из разных каналов, просит ИИ:
- выбрать топ-N самых интересных для аудитории турагентства
- переписать своими словами в стиле Pegas Touristik
- вернуть JSON-структуру
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)


def _build_prompt(posts: list[dict], top_n: int = 3) -> str:
    """Промпт для GigaChat: отбор + переписывание."""
    posts_block = []
    for i, p in enumerate(posts, 1):
        text = (p.get("text") or "")[:600]  # обрезаем длинные посты
        posts_block.append(
            f"---\n[{i}] @{p.get('channel_username', '?')}, {p['date'].strftime('%d.%m')} "
            f"({p.get('views', 0)} просмотров)\n"
            f"Ссылка: {p.get('post_url', '')}\n"
            f"{text}"
        )
    posts_text = "\n".join(posts_block)

    return f"""Ты — редактор Telegram-канала турагентства Pegas Touristik (Уфа). Тебе дали {len(posts)} постов из других туристических каналов за сутки. Твоя задача — отобрать топ-{top_n} самых полезных для нашей аудитории (туристы, путешественники из Уфы и регионов России) и переписать каждый своими словами.

Критерии отбора (что НУЖНО):
— Польза для путешественника-обывателя: визы, страны открыты/закрыты, новые курорты и отели, лайфхаки в поездке, безопасность, погода/сезоны
— Новые прямые рейсы (особенно из Уфы и регионов России) — «возобновили рейс», «открыли направление»
— Открытие новых стран/курортов для россиян
— Скидки и акции туроператоров на ТУРЫ (а не отдельно билеты), но не реклама конкретных турагентств-конкурентов

Что НЕ берём (категорически):
— Внутренняя кухня туроператоров и авиакомпаний: новые формы оферты, изменения договоров, B2B-соглашения, кадровые перестановки, отчёты, презентации — нашему подписчику это не интересно
— Скидки и акции авиакомпаний на отдельные билеты (Аэрофлот, S7, Победа и т.п.) — мы продаём ТУРЫ, а не билеты
— Чисто рекламные посты других турагентств
— Политика, скандалы, происшествия (если только это не критично для туриста — например, землетрясение на курорте)
— Технические новости без выгоды для туриста

Что НЕЛЬЗЯ:
— Копировать текст 1-в-1, должен быть рерайт.
— Упоминать названия каналов-источников.
— Утверждать факты которых нет в исходнике.
— Использовать <br>, <p>, <div>. Только <b> для жирного и обычные переносы строк.
— Markdown (**жирный**) — только HTML <b>.

Стиль наших постов:
— Дружелюбный, без официоза. Обращение на «вы».
— Эмодзи в начале строк (✈️🌴🔥📍 и т.п.) — но без перебора.
— ЗАГОЛОВОК ПЕРВОЙ СТРОКОЙ — обязательно в тегах <b>...</b>, ЖИРНЫМ. Без него пост не подходит.
— Сам заголовок должен ЗАЦЕПИТЬ — чтобы подписчик захотел открыть и прочитать. Не пиши просто «Новость о визах в Турцию» — это скучно. Нужно:
  • интрига, вопрос, неожиданность: «А вы знали, что теперь...» / «Пегас сделал то, чего не было 5 лет»
  • срочность, FOMO: «Срочно! С 1 мая всё меняется» / «Успейте оформить — дедлайн уже близко»
  • выгода, цифры: «-30% на туры в Египет до конца недели» / «50 000 ₽ за 11 ночей в Турции»
  • эмоция: «Наконец-то! Прямые рейсы из Уфы вернулись»
  • прямое обращение: «Если летите в августе — обязательно прочтите»
— Заголовок 5-12 слов, не больше. Эмодзи в самом начале строки (1 штука).
— После заголовка пустая строка, дальше 2-4 абзаца **через пустую строку** между ними. Каждый абзац — 2-3 предложения, НЕ длиннее 5 строк визуально. НЕ слепляй всё в один длинный текст-стену.
— Если уместно — выделяй <b>ключевые цифры/даты/названия стран</b> жирным внутри текста.
— В конце:
  📩 Написать нам: <b>@hottourpegas_bot</b>
  📞 Позвонить: <b>+7 (917) 044-21-00</b>

Исходные посты:
{posts_text}

Ответь СТРОГО в формате JSON-массива (без префикса ```json, без объяснений):
[
  {{"src": номер_исходного_поста, "title": "коротко о чём", "text": "полный текст поста для нашего канала с HTML-разметкой"}},
  ...
]
Ровно {top_n} элементов в массиве. Текст каждого поста — 400-700 символов."""


def select_and_rewrite(posts: list[dict], top_n: int = 3) -> list[dict]:
    """
    Возвращает список переписанных постов с полями: title, text, source_post (исходный dict).
    Если GigaChat недоступен — пустой список.
    """
    if not posts:
        return []
    if not os.getenv("GIGACHAT_AUTH_KEY", "").strip():
        logger.warning("Новости: GIGACHAT_AUTH_KEY не задан, пропускаю обработку")
        return []

    try:
        from ai.gigachat import generate as giga_generate
        prompt = _build_prompt(posts, top_n=top_n)
        raw = giga_generate(prompt, max_tokens=2500, temperature=0.6)
    except Exception as e:
        logger.warning(f"Новости: GigaChat не ответил: {e}")
        return []

    # Иногда модель оборачивает в markdown — отрежем
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        items = json.loads(raw)
    except Exception as e:
        logger.warning(f"Новости: не разобрал JSON от GigaChat: {e}; ответ был: {raw[:300]}")
        return []

    if not isinstance(items, list):
        return []

    result = []
    for item in items:
        try:
            src_idx = int(item.get("src", 0)) - 1  # 1-based → 0-based
            source = posts[src_idx] if 0 <= src_idx < len(posts) else None
            title = (item.get("title") or "").strip()
            text  = (item.get("text") or "").strip()

            # Гарантируем что пост начинается с <b>заголовка</b>.
            # GigaChat иногда забывает обернуть первую строку в <b>.
            text = _ensure_headline(text, title)
            # Разбиваем длинные абзацы на части по 3-5 строк, чтобы пост читался.
            text = _split_long_paragraphs(text, max_sentences=3)

            result.append({
                "title":       title,
                "text":        text,
                "source_post": source,
            })
        except Exception:
            continue
    return result


def _ensure_headline(text: str, title: str) -> str:
    """
    Если первая непустая строка не обёрнута в <b>...</b>, добавляем
    <b>title</b> сверху (или оборачиваем первую строку, если title пустой).
    Также конвертирует markdown **жирный** → <b>жирный</b> в начале текста.
    """
    if not text:
        return text

    # Markdown bold в начале → HTML bold (модель иногда смешивает форматы)
    text = re.sub(
        r"^(\s*)\*\*([^\n*]{1,200}?)\*\*",
        r"\1<b>\2</b>",
        text,
        count=1,
    )

    stripped = text.lstrip()
    # Уже есть жирный заголовок в начале — ничего не делаем
    if stripped.startswith("<b>") and "</b>" in stripped[:200]:
        return text

    # Берём первую непустую строку
    lines = text.split("\n")
    first_idx = next((i for i, ln in enumerate(lines) if ln.strip()), -1)
    if first_idx == -1:
        return text
    first = lines[first_idx].strip()

    if title:
        # Подставляем заголовок сверху, оригинальный текст оставляем как есть
        head = f"<b>{title}</b>"
        # Если первая строка совпадает с title — не дублируем
        if first.lower() == title.lower() or first.lower() == f"<b>{title}</b>".lower():
            lines[first_idx] = head
            return "\n".join(lines)
        return f"{head}\n\n{text}"

    # Title пустой — оборачиваем первую строку в <b>
    lines[first_idx] = f"<b>{first}</b>"
    return "\n".join(lines)


def _split_long_paragraphs(text: str, max_sentences: int = 3) -> str:
    """
    Разбивает длинные абзацы на части по `max_sentences` предложений
    (2-3 предложения — это примерно 3-5 строк визуально в Telegram).
    Существующие абзацы (разделённые \\n\\n) сохраняются. Короткие — не трогаем.

    Заголовок (первая строка с <b>...</b>) не считается за абзац и оставляется как есть.
    """
    if not text:
        return text

    paragraphs = text.split("\n\n")
    out: list[str] = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # Заголовок и короткие списки — не трогаем
        if para.startswith("<b>") and "</b>" in para[:200] and "\n" not in para:
            out.append(para)
            continue

        # Делим на предложения (по точке/восклицанию/вопросу + пробел/перенос)
        sentences = re.split(r"(?<=[.!?…])\s+", para)
        sentences = [s.strip() for s in sentences if s.strip()]

        if len(sentences) <= max_sentences:
            out.append(para)
            continue

        # Разбиваем на чанки по max_sentences
        chunks = []
        for i in range(0, len(sentences), max_sentences):
            chunk = " ".join(sentences[i:i + max_sentences])
            chunks.append(chunk)
        out.append("\n\n".join(chunks))

    return "\n\n".join(out)
