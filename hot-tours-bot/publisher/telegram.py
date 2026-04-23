"""
publisher/telegram.py — Публикация постов в Telegram

Умеет:
- Публиковать текст с фото в канал
- Присылать превью поста руководителю на одобрение (кнопки ✅/❌)
- Присылать уведомления о новых заявках

Документация Telegram Bot API: https://core.telegram.org/bots/api
"""

import requests
from typing import Optional


class TelegramPublisher:
    """
    Публикует посты в Telegram-канал и отправляет уведомления.

    Пример использования:
        tg = TelegramPublisher(token="...", channel_id="@my_channel")
        tg.publish(text="Горящий тур!", photo_url="https://...")
    """

    API_URL = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, channel_id: str, admin_id):
        """
        token      — токен бота от @BotFather
        channel_id — ID или username канала (например @family_tour_ufa)
        admin_id   — ID админа (int) или список ID (list[int]) для уведомлений
        """
        self.token      = token
        self.channel_id = channel_id
        # Поддерживаем одиночный ID и список
        if isinstance(admin_id, (list, tuple)):
            self.admin_ids = [int(x) for x in admin_id if x]
        else:
            self.admin_ids = [int(admin_id)] if admin_id else []
        self.admin_id = self.admin_ids[0] if self.admin_ids else 0

    def _call(self, method: str, data: dict) -> dict:
        """Делает запрос к Telegram Bot API"""
        url = self.API_URL.format(token=self.token, method=method)
        try:
            resp = requests.post(url, json=data, timeout=30)
            result = resp.json()
            if not result.get("ok"):
                print(f"⚠️  Telegram API ошибка: {result.get('description')}")
            return result
        except Exception as e:
            print(f"❌ Ошибка запроса к Telegram: {e}")
            return {}

    def publish(self, text: str, photo_url: Optional[str] = None,
                chat_id: Optional[str] = None) -> Optional[int]:
        """
        Публикует пост в канал (или в любой чат).

        Возвращает message_id опубликованного сообщения.
        """
        target = chat_id or self.channel_id

        if photo_url:
            # Пост с фото
            result = self._call("sendPhoto", {
                "chat_id":   target,
                "photo":     photo_url,
                "caption":   text,
                # parse_mode HTML позволяет использовать <b>жирный</b> текст
                "parse_mode": "HTML",
            })
        else:
            # Только текст
            result = self._call("sendMessage", {
                "chat_id":    target,
                "text":       text,
                "parse_mode": "HTML",
            })

        if result.get("ok"):
            msg_id = result["result"]["message_id"]
            print(f"✅ Telegram: опубликовано (message_id={msg_id})")
            return msg_id

        return None

    def send_approval_request(self, text: str, photo_url: Optional[str],
                               tour_id: str) -> Optional[int]:
        """
        Присылает руководителю превью поста с кнопками ✅/❌.
        Используется в режиме одобрения (первая неделя).

        tour_id нужен чтобы при нажатии ✅ знать какой тур публиковать.
        """
        preview = f"📋 <b>НОВЫЙ ГОРЯЩИЙ ТУР — на одобрение:</b>\n\n{text}"

        # Inline-кнопки под сообщением
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Опубликовать", "callback_data": f"approve_{tour_id}"},
                {"text": "❌ Пропустить",   "callback_data": f"reject_{tour_id}"},
            ]]
        }

        if photo_url:
            result = self._call("sendPhoto", {
                "chat_id":      self.admin_id,
                "photo":        photo_url,
                "caption":      preview,
                "parse_mode":   "HTML",
                "reply_markup": keyboard,
            })
        else:
            result = self._call("sendMessage", {
                "chat_id":      self.admin_id,
                "text":         preview,
                "parse_mode":   "HTML",
                "reply_markup": keyboard,
            })

        if result.get("ok"):
            msg_id = result["result"]["message_id"]
            print(f"✅ Telegram: превью отправлено руководителю (message_id={msg_id})")
            return msg_id

        return None

    def notify_admin(self, text: str) -> None:
        """Отправляет текстовое уведомление всем админам."""
        for admin_id in self.admin_ids:
            self._call("sendMessage", {
                "chat_id":    admin_id,
                "text":       text,
                "parse_mode": "HTML",
            })

    def notify_new_lead(self, name: str, phone: str, tour: str,
                         dates: str, tourists: str, budget: str,
                         source: str = "Telegram") -> None:
        """
        Присылает руководителю уведомление о новой заявке.
        Вызывается когда клиент заполнил форму в боте.
        """
        text = (
            f"🔔 <b>НОВАЯ ЗАЯВКА!</b>\n\n"
            f"👤 <b>Имя:</b> {name}\n"
            f"📞 <b>Телефон:</b> {phone}\n"
            f"✈️ <b>Тур:</b> {tour}\n"
            f"📅 <b>Даты:</b> {dates}\n"
            f"👨‍👩‍👧 <b>Туристы:</b> {tourists}\n"
            f"💰 <b>Бюджет:</b> {budget}\n"
            f"📱 <b>Источник:</b> {source}\n\n"
            f"⚡ Позвоните клиенту в течение часа!"
        )
        self.notify_admin(text)
        print(f"✅ Telegram: уведомление о заявке отправлено руководителю")
