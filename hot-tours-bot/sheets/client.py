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

SHEET_META       = "Метаданные"  # key-value для служебных меток (last news run и т.п.)
SHEET_HOTELS_DESC = "Описания отелей"  # кэш реальных описаний с tophotels.ru
HOTELS_DESC_HEADERS = [
    "Отель",            # как написано в туре
    "Страна",
    "Описание",         # форматированный текст для промпта ИИ
    "Когда обновлено",  # ДД.ММ.ГГГГ ЧЧ:ММ
]

SHEET_ERRORS = "Журнал ошибок"  # сюда пишутся WARNING/ERROR/CRITICAL для быстрой диагностики
ERRORS_HEADERS = [
    "Время",      # ДД.ММ.ГГГГ ЧЧ:ММ:СС МСК
    "Уровень",    # WARNING / ERROR / CRITICAL
    "Источник",   # имя логгера (publisher.vk и т.п.)
    "Сообщение",  # короткое описание
    "Контекст",   # traceback при ошибке (опционально)
]
MAX_ERROR_ROWS = 1000  # после этого старые автоматически чистятся

SHEET_NEWS_SOURCES = "Источники новостей"
NEWS_SOURCES_HEADERS = [
    "Канал",     # ссылка вида https://t.me/atorus_news или просто atorus_news
    "Категория", # туризм / визы / отели / лайфхаки / разное
    "Активен",   # да / нет
]

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

    def _get_meta_ws(self):
        ss = self._get_spreadsheet()
        if not ss:
            return None
        try:
            return ss.worksheet(SHEET_META)
        except Exception:
            try:
                ws = ss.add_worksheet(title=SHEET_META, rows=50, cols=2)
                ws.append_row(["Ключ", "Значение"])
                return ws
            except Exception as e:
                print(f"❌ Sheets: не смог создать '{SHEET_META}': {e}")
                return None

    def get_meta(self, key: str) -> str:
        """Читает значение по ключу из листа 'Метаданные'."""
        ws = self._get_meta_ws()
        if not ws:
            return ""
        try:
            for row in ws.get_all_records():
                if str(row.get("Ключ", "")).strip() == key:
                    return str(row.get("Значение", "")).strip()
        except Exception as e:
            print(f"❌ Sheets: ошибка чтения meta {key}: {e}")
        return ""

    def set_meta(self, key: str, value: str) -> None:
        """Записывает/обновляет значение по ключу."""
        ws = self._get_meta_ws()
        if not ws:
            return
        try:
            cells = ws.col_values(1)
            for i, k in enumerate(cells, start=1):
                if k == key:
                    ws.update_cell(i, 2, value)
                    return
            ws.append_row([key, value])
        except Exception as e:
            print(f"❌ Sheets: ошибка записи meta {key}: {e}")

    # ── Кэш описаний отелей с tophotels.ru ───────────────────────

    def _get_hotels_desc_ws(self):
        ss = self._get_spreadsheet()
        if not ss:
            return None
        try:
            return ss.worksheet(SHEET_HOTELS_DESC)
        except Exception:
            try:
                ws = ss.add_worksheet(
                    title=SHEET_HOTELS_DESC,
                    rows=500,
                    cols=len(HOTELS_DESC_HEADERS),
                )
                ws.append_row(HOTELS_DESC_HEADERS)
                ws.format(
                    f"A1:{chr(64 + len(HOTELS_DESC_HEADERS))}1",
                    {"textFormat": {"bold": True}},
                )
                return ws
            except Exception as e:
                print(f"❌ Sheets: не смог создать '{SHEET_HOTELS_DESC}': {e}")
                return None

    @staticmethod
    def _norm(s: str) -> str:
        return str(s or "").strip().lower()

    def get_hotel_description(self, hotel_name: str, country: str = "") -> str:
        """Возвращает закешированное описание отеля или пустую строку."""
        ws = self._get_hotels_desc_ws()
        if not ws:
            return ""
        target_hotel = self._norm(hotel_name)
        target_country = self._norm(country)
        try:
            for row in ws.get_all_records():
                if (self._norm(row.get("Отель")) == target_hotel and
                        self._norm(row.get("Страна")) == target_country):
                    return str(row.get("Описание", "")).strip()
        except Exception as e:
            print(f"❌ Sheets: ошибка чтения описаний отелей: {e}")
        return ""

    def set_hotel_description(self, hotel_name: str, country: str,
                               description: str) -> None:
        """Записывает или обновляет описание отеля в кэше."""
        ws = self._get_hotels_desc_ws()
        if not ws:
            return
        from datetime import datetime
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        target_hotel = self._norm(hotel_name)
        target_country = self._norm(country)
        try:
            rows = ws.get_all_records()
            for i, row in enumerate(rows, start=2):
                if (self._norm(row.get("Отель")) == target_hotel and
                        self._norm(row.get("Страна")) == target_country):
                    ws.update_cell(i, 3, description)
                    ws.update_cell(i, 4, now)
                    return
            ws.append_row([hotel_name, country, description, now])
        except Exception as e:
            print(f"❌ Sheets: ошибка записи описания отеля: {e}")

    def get_news_sources(self) -> list[str]:
        """
        Возвращает список активных каналов-источников новостей.
        Если листа нет — создаёт его с примерами.
        """
        ss = self._get_spreadsheet()
        if not ss:
            return []
        try:
            ws = ss.worksheet(SHEET_NEWS_SOURCES)
        except Exception:
            try:
                ws = ss.add_worksheet(title=SHEET_NEWS_SOURCES, rows=50, cols=len(NEWS_SOURCES_HEADERS))
                ws.append_row(NEWS_SOURCES_HEADERS)
                # Несколько примеров — пользователь сможет редактировать
                ws.append_row(["https://t.me/atorus_news", "туризм", "нет"])
                ws.append_row(["https://t.me/Travel_Russia", "туризм", "нет"])
                ws.append_row(["https://t.me/sletat_ru", "туризм", "нет"])
            except Exception as e:
                print(f"❌ Sheets: не смог создать '{SHEET_NEWS_SOURCES}': {e}")
                return []

        try:
            rows = ws.get_all_records()
            return [
                str(r.get("Канал", "")).strip()
                for r in rows
                if str(r.get("Активен", "")).strip().lower() in ("да", "yes", "true", "1")
                and r.get("Канал")
            ]
        except Exception as e:
            print(f"❌ Sheets: ошибка чтения источников новостей: {e}")
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

    # ── Лист "Журнал ошибок" ─────────────────────────────────────

    def _get_errors_ws(self):
        """Возвращает worksheet журнала ошибок, создаёт лист если его нет."""
        ss = self._get_spreadsheet()
        if not ss:
            return None
        try:
            return ss.worksheet(SHEET_ERRORS)
        except Exception:
            try:
                ws = ss.add_worksheet(title=SHEET_ERRORS, rows=MAX_ERROR_ROWS + 100,
                                       cols=len(ERRORS_HEADERS))
                ws.append_row(ERRORS_HEADERS)
                ws.format(f"A1:{chr(64 + len(ERRORS_HEADERS))}1",
                          {"textFormat": {"bold": True}})
                return ws
            except Exception as e:
                print(f"❌ Sheets: не смог создать '{SHEET_ERRORS}': {e}")
                return None

    def append_error_logs(self, entries: list[dict]) -> bool:
        """
        Добавляет batch записей в журнал ошибок одним API-вызовом.
        entries — список dict с полями time, level, logger, message, context.
        Возвращает True если успешно.
        """
        if not entries:
            return True
        ws = self._get_errors_ws()
        if not ws:
            return False
        try:
            rows = [
                [
                    e.get("time", ""),
                    e.get("level", ""),
                    e.get("logger", ""),
                    (e.get("message", "") or "")[:1000],   # cap длины ячейки
                    (e.get("context", "") or "")[:2000],
                ]
                for e in entries
            ]
            ws.append_rows(rows, value_input_option="RAW")

            # Чистка старых: если строк > MAX_ERROR_ROWS — удаляем самые ранние
            try:
                total = ws.row_count
                values = ws.col_values(1)  # только колонка времени, дешевле
                actual_rows = len([v for v in values if v]) - 1  # минус заголовок
                if actual_rows > MAX_ERROR_ROWS:
                    excess = actual_rows - MAX_ERROR_ROWS
                    # Удаляем строки 2..2+excess (после заголовка)
                    ws.delete_rows(2, 1 + excess)
            except Exception:
                pass  # чистка не критична
            return True
        except Exception as e:
            print(f"❌ Sheets: не смог записать журнал ошибок: {e}")
            return False

    def get_recent_errors_from_sheet(self, limit: int = 10) -> list[dict]:
        """Возвращает последние N записей из журнала ошибок (для команды /errors при пустом буфере)."""
        ws = self._get_errors_ws()
        if not ws:
            return []
        try:
            rows = ws.get_all_records()
            return rows[-limit:] if rows else []
        except Exception as e:
            print(f"❌ Sheets: ошибка чтения журнала ошибок: {e}")
            return []

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
