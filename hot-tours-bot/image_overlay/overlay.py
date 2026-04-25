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
        logger.info("Overlay: Pillow импортирован")
    except ImportError as ie:
        logger.warning(f"Overlay: Pillow НЕ установлен ({ie}), фото без наложения")
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

        # ── Шрифты ──
        bold_path = _find_font(FONT_PATHS_BOLD)
        reg_path  = _find_font(FONT_PATHS_REGULAR) or bold_path
        if not bold_path:
            logger.warning(f"Overlay: НЕ НАЙДЕН TTF-шрифт. Пробовал: {FONT_PATHS_BOLD}")
            return image_bytes
        logger.info(f"Overlay: используется шрифт {bold_path}")

        # Размеры — пропорция от ширины
        sz_hot     = int(W * 0.038)   # "ГОРЯЩИЙ ТУР"
        sz_country = int(W * 0.080)   # страна — самое большое
        sz_price   = int(W * 0.058)   # цена
        sz_small   = int(W * 0.030)   # дата / город

        try:
            f_hot     = ImageFont.truetype(bold_path, sz_hot)
            f_country = ImageFont.truetype(bold_path, sz_country)
            f_price   = ImageFont.truetype(bold_path, sz_price)
            f_small   = ImageFont.truetype(reg_path, sz_small)
        except Exception as fe:
            logger.warning(f"Ошибка загрузки шрифта: {fe}")
            return image_bytes

        padding_x = int(W * 0.05)
        gap_tag    = int(H * 0.018)
        gap_line   = int(H * 0.012)

        # Считаем суммарную высоту блока, чтобы прижать снизу с одинаковым отступом
        tag_pad   = int(sz_hot * 0.35)
        tag_h_box = sz_hot + tag_pad * 2

        country_text = (country or "").upper()
        price_text   = f"от {price}" if price else ""
        dep_text     = departure or ""

        total_h = tag_h_box
        if country_text:
            total_h += gap_tag + sz_country
        if price_text:
            total_h += gap_line + sz_price
        if dep_text:
            total_h += gap_line + sz_small

        bottom_margin = int(H * 0.06)  # отступ от нижнего края картинки
        gradient_h    = total_h + int(H * 0.10) + bottom_margin
        y_start       = H - bottom_margin - total_h

        # ── Градиент снизу ──
        gradient = Image.new("RGBA", (W, gradient_h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(gradient)
        for y in range(gradient_h):
            alpha = int(220 * (y / gradient_h) ** 1.6)
            gd.rectangle([(0, y), (W, y + 1)], fill=(0, 0, 0, alpha))

        img = img.convert("RGBA")
        img.alpha_composite(gradient, dest=(0, max(0, H - gradient_h)))

        draw = ImageDraw.Draw(img)

        # ── Текст ──
        y = y_start

        # Красная плашка "ГОРЯЩИЙ ТУР"
        tag = "ГОРЯЩИЙ ТУР"
        try:
            tag_bbox = draw.textbbox((0, 0), tag, font=f_hot)
            tag_w = tag_bbox[2] - tag_bbox[0]
        except Exception:
            tag_w = int(sz_hot * len(tag) * 0.55)
        draw.rectangle(
            [(padding_x, y), (padding_x + tag_w + tag_pad * 2, y + tag_h_box)],
            fill=(220, 50, 50)
        )
        draw.text((padding_x + tag_pad, y + tag_pad), tag, font=f_hot, fill=(255, 255, 255))
        y += tag_h_box + gap_tag

        # Страна
        if country_text:
            draw.text((padding_x, y), country_text, font=f_country, fill=(255, 255, 255),
                      stroke_width=3, stroke_fill=(0, 0, 0))
            y += sz_country + gap_line

        # Цена
        if price_text:
            draw.text((padding_x, y), price_text, font=f_price, fill=(255, 220, 80),
                      stroke_width=2, stroke_fill=(0, 0, 0))
            y += sz_price + gap_line

        # Дата / город
        if dep_text:
            draw.text((padding_x, y), dep_text, font=f_small, fill=(230, 230, 230),
                      stroke_width=1, stroke_fill=(0, 0, 0))

        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=92)
        return out.getvalue()

    except Exception as e:
        logger.warning(f"Ошибка наложения текста на фото: {e}, возвращаю оригинал")
        return image_bytes
