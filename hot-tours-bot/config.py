"""
config.py — Настройки приложения

Читает переменные из файла .env и делает их доступными
для всех модулей проекта. Меняешь .env — меняется поведение бота.
"""

import os
import json
from dotenv import load_dotenv

# Загружаем переменные из файла .env в текущую папке
load_dotenv()

# Если на сервере задана переменная GOOGLE_CREDENTIALS_JSON —
# записываем её в файл (нужно для Railway/облака)
_creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
if _creds_json:
    _creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "google_credentials.json")
    with open(_creds_file, "w", encoding="utf-8") as _f:
        _f.write(_creds_json)


class Config:
    # ── Tourvisor ──────────────────────────────────────────────
    TOURVISOR_LOGIN    = os.getenv("TOURVISOR_LOGIN", "")
    TOURVISOR_PASSWORD = os.getenv("TOURVISOR_PASSWORD", "")

    # ── Telegram ───────────────────────────────────────────────
    # TELEGRAM_CHANNEL_ID       — канал для НОВОСТЕЙ (старый family_tour_channel)
    # TELEGRAM_TOURS_CHANNEL_ID — канал для ГОРЯЩИХ ТУРОВ (новый, обязательно)
    # Если TOURS не задан — все посты идут в TELEGRAM_CHANNEL_ID (как раньше).
    TELEGRAM_BOT_TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHANNEL_ID       = os.getenv("TELEGRAM_CHANNEL_ID", "")
    TELEGRAM_TOURS_CHANNEL_ID = os.getenv("TELEGRAM_TOURS_CHANNEL_ID", "") or os.getenv("TELEGRAM_CHANNEL_ID", "")
    # TELEGRAM_ADMIN_ID может содержать несколько ID через запятую: "418012639,999999999"
    TELEGRAM_ADMIN_IDS = [int(x.strip()) for x in os.getenv("TELEGRAM_ADMIN_ID", "0").split(",") if x.strip().lstrip("-").isdigit()]
    TELEGRAM_ADMIN_ID  = TELEGRAM_ADMIN_IDS[0] if TELEGRAM_ADMIN_IDS else 0

    # ── ВКонтакте ──────────────────────────────────────────────
    # У VK один канал — публикуется и туры, и новости.
    VK_TOKEN      = os.getenv("VK_TOKEN", "")       # токен группы — для публикации постов
    VK_USER_TOKEN = os.getenv("VK_USER_TOKEN", "")  # токен пользователя — для загрузки фото
    VK_GROUP_ID   = int(os.getenv("VK_GROUP_ID", "0"))

    # ── MAX (мессенджер от VK) ─────────────────────────────────
    # MAX_CHAT_ID       — канал для НОВОСТЕЙ (старый)
    # MAX_TOURS_CHAT_ID — канал для ГОРЯЩИХ ТУРОВ (новый, обязательно)
    # Если TOURS не задан — все посты идут в MAX_CHAT_ID.
    MAX_TOKEN         = os.getenv("MAX_TOKEN", "").strip()
    MAX_CHAT_ID       = int(os.getenv("MAX_CHAT_ID", "0"))
    MAX_TOURS_CHAT_ID = int(os.getenv("MAX_TOURS_CHAT_ID", "0") or os.getenv("MAX_CHAT_ID", "0"))

    # ── Claude API ─────────────────────────────────────────────
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    # ── Google Sheets ──────────────────────────────────────────
    GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "google_credentials.json")
    GOOGLE_SHEET_ID         = os.getenv("GOOGLE_SHEET_ID", "")

    # ── Фильтры туров ──────────────────────────────────────────
    MIN_DISCOUNT_PERCENT = int(os.getenv("MIN_DISCOUNT_PERCENT", "20"))
    MAX_PRICE            = int(os.getenv("MAX_PRICE", "150000"))
    DAYS_AHEAD           = int(os.getenv("DAYS_AHEAD", "14"))
    NIGHTS_FROM          = int(os.getenv("NIGHTS_FROM", "7"))
    NIGHTS_TO            = int(os.getenv("NIGHTS_TO", "14"))

    # ── Расписание публикаций (часы, по московскому времени) ───
    PUBLISH_HOURS = [9, 14, 19]

    # ── Города вылета (коды Tourvisor) — заполним на шаге 1 ───
    # Уфа и соседние города. Коды получим от API.
    DEPARTURE_CITY_CODES = []  # заполнится автоматически

    @classmethod
    def validate(cls):
        """Проверяет что все обязательные переменные заданы"""
        required = {
            "TOURVISOR_LOGIN":    cls.TOURVISOR_LOGIN,
            "TOURVISOR_PASSWORD": cls.TOURVISOR_PASSWORD,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(
                f"❌ Не заданы переменные в .env: {', '.join(missing)}\n"
                f"Скопируй .env.example в .env и заполни значения."
            )
        print("✅ Конфиг загружен успешно")
