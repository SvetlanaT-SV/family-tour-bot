"""
image_overlay/news_placeholder.py — генерация баннера-заглушки для новостей
без фото в источнике.

Используется когда в исходном Telegram-канале новостной пост был чисто
текстовым (нет картинки). Без заглушки наш пост улетал бы только текстом —
выглядит беднее в ленте. Заглушка — простой градиент с надписью «НОВОСТИ
ТУРИЗМА» (или категорией) и эмодзи-иконкой.

Размер 1080×540 — стандартное соотношение 2:1 для превью карточек.
Градиент выбирается случайно из палитры — каждый пост-новость в ленте
смотрится «свежо», без однообразия.
"""

import io
import logging
import random
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Палитра градиентов (top, bottom RGB). Подобраны яркими и читаемыми
# для крупного белого шрифта поверх.
_BG_PALETTES = [
    ((33, 150, 243),  (3, 87, 155)),    # синий → тёмно-синий
    ((255, 152, 0),   (230, 81, 0)),    # оранжевый → насыщенный оранжевый
    ((76, 175, 80),   (27, 94, 32)),    # зелёный → тёмно-зелёный
    ((156, 39, 176),  (74, 20, 140)),   # фиолетовый
    ((244, 67, 54),   (183, 28, 28)),   # красный
    ((0, 188, 212),   (0, 96, 100)),    # бирюзовый
    ((255, 87, 34),   (191, 54, 12)),   # тёмно-оранжевый
    ((63, 81, 181),   (26, 35, 126)),   # индиго
]

# Шрифты — DejaVu Sans Bold ставится в Docker (см. Dockerfile,
# пакет fonts-dejavu-core). На локальной разработке Windows может
# не быть — берём fallback на встроенный.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
)


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _measure(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _fit_font(draw, text: str, max_width: int,
               start_size: int = 96, min_size: int = 48) -> "ImageFont.ImageFont":
    """Возвращает шрифт максимально большого размера при котором текст
    укладывается в max_width. Уменьшает шаг 4px пока не влезет."""
    size = start_size
    while size > min_size:
        f = _load_font(size)
        w, _ = _measure(draw, text, f)
        if w <= max_width:
            return f
        size -= 4
    return _load_font(min_size)


def make_news_placeholder(category: Optional[str] = None) -> bytes:
    """
    Возвращает JPEG-байты баннера 1080×540 со случайным градиентом и
    надписью. Если задана `category` — выводит её на втором ряду.
    """
    W, H = 1080, 540
    SIDE_PAD = 90  # отступ от краёв слева/справа
    USABLE_WIDTH = W - 2 * SIDE_PAD

    bg_top, bg_bot = random.choice(_BG_PALETTES)

    img = Image.new("RGB", (W, H), bg_top)
    draw = ImageDraw.Draw(img)

    # Вертикальный градиент (блоками по 8px — быстрее)
    step = 8
    for y in range(0, H, step):
        ratio = y / H
        r = int(bg_top[0] + (bg_bot[0] - bg_top[0]) * ratio)
        g = int(bg_top[1] + (bg_bot[1] - bg_top[1]) * ratio)
        b = int(bg_top[2] + (bg_bot[2] - bg_top[2]) * ratio)
        draw.rectangle([(0, y), (W, y + step)], fill=(r, g, b))

    # Заголовок — автоподгон шрифта если не влезает
    title = "НОВОСТИ ТУРИЗМА"
    title_font = _fit_font(draw, title, USABLE_WIDTH, start_size=96, min_size=52)
    tw, th = _measure(draw, title, title_font)
    title_y = (H - th) // 2 - 40

    # Тень под текстом для контраста
    draw.text(((W - tw) // 2 + 4, title_y + 4), title,
              fill=(0, 0, 0, 120), font=title_font)
    draw.text(((W - tw) // 2, title_y), title,
              fill="white", font=title_font)

    # Подзаголовок (категория или название агентства) — тоже с автоподгоном
    sub = category.upper() if category else "Pegas Touristik"
    sub_font = _fit_font(draw, sub, USABLE_WIDTH, start_size=42, min_size=28)
    sw, sh = _measure(draw, sub, sub_font)
    sub_y = title_y + th + 30
    draw.text(((W - sw) // 2, sub_y), sub,
              fill=(255, 255, 255, 220), font=sub_font)

    # Декоративная горизонтальная линия под подзаголовком
    line_w = 200
    line_y = sub_y + sh + 30
    draw.rectangle(
        [((W - line_w) // 2, line_y),
         ((W + line_w) // 2, line_y + 4)],
        fill="white",
    )

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()
