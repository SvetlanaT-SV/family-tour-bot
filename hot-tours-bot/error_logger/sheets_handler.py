"""
error_logger/sheets_handler.py — централизованный журнал ошибок в Google Sheets.

Подключает кастомный logging.Handler ко всем логгерам приложения. Все WARNING /
ERROR / CRITICAL пишутся в буфер, который периодически (раз в минуту через
JobQueue) сбрасывается в лист "Журнал ошибок" в Google Sheets.

Это позволяет:
  • Светлане видеть ошибки прямо в её обычной таблице, не залезая на Railway
  • Не терять историю при перезапусках (Railway filesystem ephemeral)
  • Анализировать тренды: что часто ломается → приоритеты на исправление

Уровни:
  WARNING  — что-то не сработало, но бот продолжает (например, фото не загрузилось)
  ERROR    — фича упала с исключением
  CRITICAL — упал бот / серьёзная авария → плюс push админу в Telegram
"""

import logging
import os
import threading
import traceback
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional


# Буфер последних записей. Тут же используется как источник для команды /errors.
_BUFFER: deque = deque(maxlen=2000)
_BUFFER_LOCK = threading.Lock()
_INSTALLED = False
_NOTIFY_ADMIN_CALLBACK = None  # type: Optional[callable]


class SheetsErrorHandler(logging.Handler):
    """
    logging.Handler который складывает записи WARNING+ в общий буфер.
    Сам не делает сетевых вызовов — flush_buffer() их выгружает в Sheets.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Время в МСК (UTC+3) — для удобства Светланы
            ts_msk = datetime.fromtimestamp(record.created, tz=timezone.utc) \
                .astimezone(timezone(timedelta(hours=3)))
            entry = {
                "time":    ts_msk.strftime("%d.%m.%Y %H:%M:%S"),
                "level":   record.levelname,
                "logger":  record.name,
                "message": self.format(record) if self.formatter else record.getMessage(),
                "context": "",
            }
            if record.exc_info:
                entry["context"] = "".join(traceback.format_exception(*record.exc_info))[:1500]

            with _BUFFER_LOCK:
                _BUFFER.append(entry)

            # CRITICAL → пушим в Telegram сразу
            if record.levelno >= logging.CRITICAL and _NOTIFY_ADMIN_CALLBACK:
                try:
                    _NOTIFY_ADMIN_CALLBACK(entry)
                except Exception:
                    pass
        except Exception:
            # Никогда не позволяем логированию падать — это сломает бот
            pass


def install(level: int = logging.WARNING, notify_admin=None) -> None:
    """
    Подключает SheetsErrorHandler к корневому логгеру. Вызывать один раз
    при старте приложения (из main.py).

    notify_admin — опциональный callable(entry: dict) — будет вызван при
    CRITICAL (для push-уведомления в Telegram).
    """
    global _INSTALLED, _NOTIFY_ADMIN_CALLBACK
    if _INSTALLED:
        return
    _NOTIFY_ADMIN_CALLBACK = notify_admin

    handler = SheetsErrorHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))

    logging.getLogger().addHandler(handler)
    _INSTALLED = True


def get_recent_errors(limit: int = 5) -> list[dict]:
    """Возвращает последние N записей из буфера (для команды /errors)."""
    with _BUFFER_LOCK:
        return list(_BUFFER)[-limit:]


def flush_buffer(sheets_client) -> int:
    """
    Сбрасывает накопленные записи в Sheets через переданный SheetsClient.
    Возвращает количество выгруженных строк.

    Дублирование между буфером и Sheets безопасно: после flush мы НЕ удаляем
    из буфера (он уже ограничен maxlen и сам ротируется). А в Sheets каждый
    flush добавляет только новые записи начиная с "хвоста" последнего успешного.
    """
    if not sheets_client:
        return 0

    with _BUFFER_LOCK:
        # Берём все записи и помечаем их как выгруженные
        # Чтобы не дублировать — отдельная очередь на отправку
        to_send = [e for e in _BUFFER if not e.get("_flushed")]
        for e in to_send:
            e["_flushed"] = True

    if not to_send:
        return 0

    try:
        ok = sheets_client.append_error_logs(to_send)
        if not ok:
            # Откатываем флаг чтобы повторить на следующем flush
            with _BUFFER_LOCK:
                for e in to_send:
                    e["_flushed"] = False
            return 0
        return len(to_send)
    except Exception as e:
        # При ошибке — тоже откатываем
        with _BUFFER_LOCK:
            for e2 in to_send:
                e2["_flushed"] = False
        # Не используем logger чтобы не зациклить (handler пишет в этот же буфер)
        print(f"⚠️  ErrorLogger: flush failed: {e}")
        return 0
