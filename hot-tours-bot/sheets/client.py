"""
sheets/client.py — Сохранение заявок в Google Sheets

Каждая заявка = одна строка в таблице.
Таблица — это наша мини-CRM: видно кто написал, что хотел, статус.

Как настроить Google Sheets:
  1. Перейди на console.cloud.google.com
  2. Создай проект → включи Google Sheets API и Google Drive API
  3. Создай сервисный аккаунт → скачай JSON-ключ → сохрани как google_credentials.json
  4. Создай Google Sheets таблицу
  5. Дай доступ сервисному аккаунту (email из JSON) как "Редактор"
  6. ID таблицы — из URL: docs.google.com/spreadsheets/d/ВОТ_ЭТО/edit

Полная инструкция: https://gspread.readthedocs.io/en/latest/oauth2.html
"""

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from typing import Optional


# Права доступа которые нам нужны
SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# Названия листов таблицы
SHEET_LEADS     = "Заявки"              # сюда пишем новые заявки от клиентов
SHEET_CLIENTS   = "Клиенты"             # обновлённые данные клиентов
SHEET_TOURS     = "Туры к публикации"   # менеджер вносит туры, бот публикует
SHEET_SCHEDULED = "Расписание"          # запланированные посты, переживают перезапуск Railway

SCHEDULED_HEADERS = [
    "Когда",        # ISO-дата UTC, например 2026-04-26T19:00:00+00:00
    "Когда МСК",    # человекочитаемо: 26.04 19:00 МСК
    "tour_id",      # из PENDING_POSTS — sheets_5 / tv_xxx
    "Статус",       # ОЖИДАЕТ / ОПУБЛИКОВАН / ОТМЕНЁН
    "Страна",
    "Цена",
    "Дата вылета",
    "Текст",        # HTML-текст поста
    "Photo URL",    # URL картинки (если есть)
    "Photo bytes",  # base64 фото (если photo_url пустой)
    "Overlay страна",
    "Overlay цена",
    "Overlay вылет",
]

# Заголовки листа "Туры к публикации"
TOURS_HEADERS = [
    "Статус",            # НОВЫЙ / ПУБЛИКУЕТСЯ / ОПУБЛИКОВАН / ОШИБКА
    "Страна",            # Турция
    "Курорт",            # Анталья
    "Отель",             # Rixos Premium Belek
    "Питание",           # Всё включено, Полупансион...
    "Дата вылета",       # 15.04.2026
    "Ночей",             # 7
    "Цена/чел",          # 45000
    "Город вылета",      # Уфа (по умолчанию), Казань, Москва...
    "Особенности отеля", # факты, которые можно упоминать в посте: "первая линия, аквапарк, ультра все включено"
    "Фото URL",          # ссылка на фото (необязательно)
    "Ссылка",            # ссылка для бронирования (необязательно)
    "Опубликован",       # дата/время публикации (заполняет бот)
    "Ошибка",            # текст ошибки (заполняет бот)
]

# Заголовки колонок в листе "Заявки"
LEADS_HEADERS = [
    "Дата",
    "Имя",
    "Телефон",
    "Тур (интерес)",
    "Даты",
    "Туристы",
    "Бюджет",
    "Telegram ID",
    "Telegram username",
    "Источник",
    "Статус",
    "Комментарий",
]


class SheetsClient:
    """
    Клиент для работы с Google Sheets.

    Пример:
        sheets = SheetsClient("google_credentials.json", "sheet_id_here")
        sheets.add_lead({...})
    """

    def __init__(self, credentials_file: str, sheet_id: str):
        self.sheet_id = sheet_id
        try:
            creds = Credentials.from_service_account_file(
                credentials_file, scopes=SCOPES
            )
            self.gc = gspread.authorize(creds)
            self._ensure_sheets_exist()
        except FileNotFoundError:
            print(f"⚠️  Файл {credentials_file} не найден.")
            print("   Следуй инструкции в sheets/client.py чтобы настроить Google Sheets")
            self.gc = None

    def _get_spreadsheet(self):
        """Открывает таблицу по ID"""
        if not self.gc:
            return None
        try:
            return self.gc.open_by_key(self.sheet_id)
        except Exception as e:
            print(f"⚠️  Не удалось открыть таблицу: {e}")
            return None

    def _ensure_sheets_exist(self):
        """
        Создаёт листы с заголовками если их ещё нет.
        Запускается один раз при первом подключении.
        """
        ss = self._get_spreadsheet()
        if not ss:
            return

        existing = [ws.title for ws in ss.worksheets()]

        # Создаём лист "Заявки" если нет
        if SHEET_LEADS not in existing:
            ws = ss.add_worksheet(title=SHEET_LEADS, rows=1000, cols=20)
            ws.append_row(LEADS_HEADERS)
            # Жирные заголовки
            ws.format("A1:L1", {"textFormat": {"bold": True}})
            print(f"✅ Sheets: создан лист '{SHEET_LEADS}'")

        # Создаём лист "Клиенты" если нет
        if SHEET_CLIENTS not in existing:
            ss.add_worksheet(title=SHEET_CLIENTS, rows=1000, cols=20)
            print(f"✅ Sheets: создан лист '{SHEET_CLIENTS}'")

        # Создаём лист "Туры к публикации" если нет
        if SHEET_TOURS not in existing:
            ws = ss.add_worksheet(title=SHEET_TOURS, rows=500, cols=len(TOURS_HEADERS))
            ws.append_row(TOURS_HEADERS)
            ws.format(f"A1:{chr(64 + len(TOURS_HEADERS))}1", {"textFormat": {"bold": True}})
            print(f"✅ Sheets: создан лист '{SHEET_TOURS}'")

    def add_lead(self, lead: dict) -> bool:
        """
        Добавляет новую заявку строкой в лист "Заявки".

        lead — словарь с полями:
            name, phone, tour, dates, tourists, budget,
            tg_id, tg_user, source
        """
        ss = self._get_spreadsheet()
        if not ss:
            return False

        try:
            ws = ss.worksheet(SHEET_LEADS)
            row = [
                datetime.now().strftime("%d.%m.%Y %H:%M"),
                lead.get("name", "—"),
                lead.get("phone", "—"),
                lead.get("tour", "—"),
                lead.get("dates", "—"),
                lead.get("tourists", "—"),
                lead.get("budget", "—"),
                lead.get("tg_id", "—"),
                lead.get("tg_user", "—"),
                lead.get("source", "—"),
                "Новая",    # начальный статус
                "",         # комментарий — заполняет менеджер
            ]
            ws.append_row(row)
            print(f"✅ Sheets: заявка добавлена — {lead.get('name')}")
            return True
        except Exception as e:
            print(f"❌ Sheets: ошибка добавления заявки: {e}")
            return False

    def update_lead_status(self, row_number: int,
                            status: str, comment: str = "") -> bool:
        """
        Обновляет статус заявки.
        Менеджер меняет статус после звонка: Новая → В работе → Куплено/Отказ
        """
        ss = self._get_spreadsheet()
        if not ss:
            return False

        try:
            ws = ss.worksheet(SHEET_LEADS)
            # Колонки K=11 (Статус) и L=12 (Комментарий)
            ws.update_cell(row_number, 11, status)
            if comment:
                ws.update_cell(row_number, 12, comment)
            return True
        except Exception as e:
            print(f"❌ Sheets: ошибка обновления статуса: {e}")
            return False

    # ── Методы для работы с турами к публикации ──────────────────

    def get_pending_tours(self) -> list[dict]:
        """
        Возвращает туры со статусом 'НОВЫЙ' из листа 'Туры к публикации'.
        Бот вызывает этот метод каждые 5 минут.
        """
        ss = self._get_spreadsheet()
        if not ss:
            return []
        try:
            ws = ss.worksheet(SHEET_TOURS)
            rows = ws.get_all_records()
            pending = []
            for i, row in enumerate(rows, start=2):  # строка 1 — заголовки
                if str(row.get("Статус", "")).strip().upper() == "НОВЫЙ":
                    row["_row_number"] = i
                    pending.append(row)
            return pending
        except Exception as e:
            print(f"❌ Sheets: ошибка чтения туров: {e}")
            return []

    def mark_tour_status(self, row_number: int, status: str,
                          published_at: str = "", error: str = "") -> None:
        """
        Обновляет статус тура после публикации.
        Находит колонки по их именам в первой строке — не зависит от порядка.
        """
        ss = self._get_spreadsheet()
        if not ss:
            return
        try:
            ws = ss.worksheet(SHEET_TOURS)
            headers = ws.row_values(1)

            def col_idx(name: str) -> int:
                """Возвращает 1-based индекс колонки по её названию, или 0 если нет."""
                try:
                    return headers.index(name) + 1
                except ValueError:
                    return 0

            status_col = col_idx("Статус") or 1
            ws.update_cell(row_number, status_col, status)

            if published_at:
                c = col_idx("Опубликован")
                if c:
                    ws.update_cell(row_number, c, published_at)
            if error:
                c = col_idx("Ошибка")
                if c:
                    ws.update_cell(row_number, c, error)
        except Exception as e:
            print(f"❌ Sheets: ошибка обновления статуса тура: {e}")

    def mark_tour_publishing(self, row_number: int) -> None:
        """Ставит статус 'ПУБЛИКУЕТСЯ' — защита от двойной публикации"""
        self.mark_tour_status(row_number, "ПУБЛИКУЕТСЯ")

    # ── Лист "Расписание" — запланированные посты ───────────────

    def _get_scheduled_ws(self):
        """Возвращает worksheet расписания, создаёт лист если его нет."""
        ss = self._get_spreadsheet()
        if not ss:
            return None
        try:
            return ss.worksheet(SHEET_SCHEDULED)
        except Exception:
            try:
                ws = ss.add_worksheet(title=SHEET_SCHEDULED, rows=200, cols=len(SCHEDULED_HEADERS))
                ws.append_row(SCHEDULED_HEADERS)
                return ws
            except Exception as e:
                print(f"❌ Sheets: не удалось создать лист '{SHEET_SCHEDULED}': {e}")
                return None

    def add_scheduled_post(self, entry: dict) -> bool:
        """Добавляет запись в лист 'Расписание'."""
        ws = self._get_scheduled_ws()
        if not ws:
            return False
        try:
            row = [
                entry.get("scheduled_for", ""),
                entry.get("scheduled_for_msk", ""),
                entry.get("tour_id", ""),
                "ОЖИДАЕТ",
                entry.get("country", ""),
                entry.get("price", ""),
                entry.get("date", ""),
                entry.get("text", ""),
                entry.get("photo_url", ""),
                entry.get("photo_b64", ""),
                entry.get("overlay_country", ""),
                entry.get("overlay_price", ""),
                entry.get("overlay_departure", ""),
            ]
            ws.append_row(row, value_input_option="RAW")
            return True
        except Exception as e:
            print(f"❌ Sheets: не удалось добавить в расписание: {e}")
            return False

    def get_pending_scheduled(self) -> list[dict]:
        """Возвращает все записи со статусом ОЖИДАЕТ. К каждой добавляет _row_number."""
        ws = self._get_scheduled_ws()
        if not ws:
            return []
        try:
            rows = ws.get_all_records()
            result = []
            for i, r in enumerate(rows, start=2):
                if str(r.get("Статус", "")).strip().upper() == "ОЖИДАЕТ":
                    r["_row_number"] = i
                    result.append(r)
            return result
        except Exception as e:
            print(f"❌ Sheets: ошибка чтения расписания: {e}")
            return []

    def mark_scheduled_status(self, row_number: int, status: str) -> None:
        """Меняет статус строки расписания (ОПУБЛИКОВАН/ОТМЕНЁН)."""
        ws = self._get_scheduled_ws()
        if not ws:
            return
        try:
            headers = ws.row_values(1)
            try:
                col = headers.index("Статус") + 1
            except ValueError:
                col = 4  # дефолт
            ws.update_cell(row_number, col, status)
        except Exception as e:
            print(f"❌ Sheets: ошибка обновления статуса расписания: {e}")

    def get_all_leads(self) -> list[dict]:
        """
        Возвращает все заявки из таблицы.
        Используется для рассылок и аналитики.
        """
        ss = self._get_spreadsheet()
        if not ss:
            return []

        try:
            ws = ss.worksheet(SHEET_LEADS)
            records = ws.get_all_records()
            return records
        except Exception as e:
            print(f"❌ Sheets: ошибка чтения: {e}")
            return []
