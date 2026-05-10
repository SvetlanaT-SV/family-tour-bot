"""Журнал ошибок: пишет WARNING/ERROR/CRITICAL в Google Sheets."""
from .sheets_handler import (
    SheetsErrorHandler,
    install as install_error_logger,
    flush_buffer,
    get_recent_errors,
)
