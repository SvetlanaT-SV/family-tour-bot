"""
image_overlay/overlay.py — наложение текста на фото тура.

Рисует в нижней части фото тёмный градиент и текст:
    🔥 ГОРЯЩИЙ ТУР
    {СТРАНА}
    от {ЦЕНА}
"""

import io
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


# Порядок поиска шрифтов с поддержкой кириллицы.
# На Railway (Debian) /usr/share/fonts/truetype/dejavu/ обычно есть.
FONT_PATHS_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
FONT_PATHS_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _find_font(paths: list[str]):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def add_tour_overlay(image_bytes: bytes, country: str,
                     price: str, departure: str = "") -> bytes:
    """
    Накладывает на фото тёмный градиент снизу и три строки текста.

    image_bytes — исходное фото (JPEG/PNG) как bytes
    country     — название страны/направления
    price       — цена (например "45 000 ₽/чел")
    departure   — опциональная строка с датой/городом ("29 апр из Уфы")

    Возвращает модифицированное фото как JPEG bytes.
    Если Pillow не установлен или ошибка — возвращает оригинал без изменений.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except ImportError:
        logger.warning("Pillow не установлен, фото без наложения")
        return image_bytes

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        W, H = img.size

        # Если фото слишком маленькое — увеличиваем
        min_w = 1080
        if W < min_w:
            scale = min_w / W
            img = img.resize((int(W * scale), int(H * scale)), Image.LANCZOS)
            W, H = img.size

        # ── Градиент снизу: тёмный полупрозрачный ──
        gradient_h = int(H * 0.45)
        gradient = Image.new("RGBA", (W, gradient_h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(gradient)
        for y in range(gradient_h):
            # прозрачность растёт от 0 сверху до ~220 снизу
            alpha = int(220 * (y / gradient_h) ** 1.6)
            gd.rectangle([(0, y), (W, y + 1)], fill=(0, 0, 0, alpha))

        img = img.convert("RGBA")
        img.alpha_composite(gradient, dest=(0, H - gradient_h))

        draw = ImageDraw.Draw(img)

        # Шрифты — масштабируются от ширины картинки
        bold_path = _find_font(FONT_PATHS_BOLD)
        reg_path  = _find_font(FONT_PATHS_REGULAR) or bold_path
        if not bold_path:
            logger.warning("Не найден TTF-шрифт с кириллицей, наложение отключено")
            return image_bytes

        # Размеры (в пикселях) подбираем от ширины
        sz_hot    = int(W * 0.045)   # "🔥 ГОРЯЩИЙ ТУР"
        sz_country = int(W * 0.095)  # страна — самая большая
        sz_price  = int(W * 0.070)   # цена
        sz_small  = int(W * 0.035)   # доп. строка

        try:
            f_hot     = ImageFont.truetype(bold_path, sz_hot)
            f_country = ImageFont.truetype(bold_path, sz_country)
            f_price   = ImageFont.truetype(bold_path, sz_price)
            f_small   = ImageFont.truetype(reg_path, sz_small)
        except Exception as fe:
            logger.warning(f"Ошибка загрузки шрифта: {fe}")
            return image_bytes

        padding_x = int(W * 0.05)
        y = H - gradient_h + int(gradient_h * 0.25)

        # Заголовок
        hot_text = "🔥 ГОРЯЩИЙ ТУР"
        draw.text((padding_x, y), hot_text, font=f_hot, fill=(255, 200, 50))
        y += sz_hot + int(H * 0.01)

        # Страна
        country_text = (country or "").upper()
        draw.text((padding_x, y), country_text, font=f_country, fill=(255, 255, 255),
                  stroke_width=2, stroke_fill=(0, 0, 0))
        y += sz_country + int(H * 0.01)

        # Цена
        if price:
            price_text = f"от {price}"
            draw.text((padding_x, y), price_text, font=f_price, fill=(255, 235, 100),
                      stroke_width=2, stroke_fill=(0, 0, 0))
            y += sz_price + int(H * 0.005)

        # Дополнительная строка (дата/город вылета)
        if departure:
            draw.text((padding_x, y), departure, font=f_small, fill=(220, 220, 220))

        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=92)
        return out.getvalue()

    except Exception as e:
        logger.warning(f"Ошибка наложения текста на фото: {e}, возвращаю оригинал")
        return image_bytes
