"""
publisher/max.py — Публикация постов в MAX (мессенджер от VK)

Публикует пост с фото в канал MAX.

Документация MAX API: https://dev.max.ru/docs-api
"""

import logging
import re
import requests
from typing import Optional

logger = logging.getLogger(__name__)


class MAXPublisher:
    """
    Публикует посты в канал MAX.

    Пример:
        max_pub = MAXPublisher(token="ваш_токен", chat_id=123456789)
        max_pub.publish("Горящий тур!", photo_url="https://...")
    """

    API_URL = "https://platform-api.max.ru"

    def __init__(self, token: str, chat_id: int):
        self.chat_id = chat_id
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": token,
        })

    def _call(self, method: str, endpoint: str, **kwargs) -> dict:
        """Делает запрос к MAX API."""
        url = f"{self.API_URL}{endpoint}"
        try:
            resp = self.session.request(method, url, timeout=30, **kwargs)
            result = resp.json()
            if resp.status_code >= 400:
                logger.warning(f"MAX API ошибка {resp.status_code}: {result}")
            return result
        except Exception as e:
            logger.warning(f"Ошибка запроса к MAX: {e}")
            return {}

    def _upload_photo(self, photo_url: str) -> Optional[dict]:
        """
        Загружает фото в MAX в два шага:
          1. Получаем URL для загрузки через POST /uploads?type=image
          2. Загружаем файл по этому URL через multipart/form-data
        Возвращает dict с данными фото для вложения (token или url).
        """
        # Шаг 1: получаем адрес сервера для загрузки
        upload_data = self._call("POST", "/uploads", params={"type": "image"})
        upload_url = upload_data.get("url")
        if not upload_url:
            logger.warning(f"MAX: не получили upload URL: {upload_data}")
            return None

        # Шаг 2: скачиваем фото по внешней ссылке
        try:
            img_resp = requests.get(photo_url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0"
            })
            img_resp.raise_for_status()
        except Exception as e:
            logger.warning(f"MAX: не удалось скачать фото: {e}")
            return None

        # Шаг 3: загружаем на сервер MAX
        try:
            upload_resp = requests.post(
                upload_url,
                files={"file": ("photo.jpg", img_resp.content, "image/jpeg")},
                timeout=30,
            )
            upload_resp.raise_for_status()
            result = upload_resp.json()
            logger.info(f"MAX: фото загружено, ответ: {result}")
            return result
        except Exception as e:
            logger.warning(f"MAX: не удалось загрузить фото: {e}")
            return None

    def publish(self, text: str, photo_url: Optional[str] = None) -> Optional[str]:
        """
        Публикует пост в канал MAX.
        Возвращает mid (ID сообщения) при успехе.
        """
        # Заменяем ссылку на Telegram бота на ссылку на MAX бота
        max_text = text.replace(
            "📩 Написать нам: <b>@hottourpegas_bot</b>",
            "📩 Написать нам: <b>max.ru/id027708174835_bot</b>",
        )
        # Убираем HTML-теги кроме <b> и <i> — MAX поддерживает только их
        max_text = re.sub(r"<(?!/?b>|/?i>)[^>]+>", "", max_text)

        body: dict = {
            "text": max_text[:4000],
            "format": "html",
            "notify": True,
        }

        # Если есть фото — загружаем и добавляем как вложение
        if photo_url:
            photo_data = self._upload_photo(photo_url)
            if photo_data:
                # MAX API возвращает photos[0].token или url после загрузки
                photos = photo_data.get("photos", [])
                if photos:
                    token = photos[0].get("token")
                    if token:
                        body["attachments"] = [{"type": "image", "payload": {"token": token}}]
                # Если структура другая — пробуем url напрямую
                elif photo_data.get("url"):
                    body["attachments"] = [{"type": "image", "payload": {"url": photo_data["url"]}}]

        logger.info(f"MAX: публикуем в chat_id={self.chat_id}")
        result = self._call(
            "POST", "/messages",
            params={"chat_id": self.chat_id},
            json=body,
        )

        logger.info(f"MAX: ответ API: {result}")

        # mid находится в message.body.mid
        mid = result.get("message", {}).get("body", {}).get("mid")
        if mid:
            logger.info(f"MAX: опубликовано (mid={mid})")
            return mid

        logger.warning(f"MAX: mid не найден в ответе: {result}")
        return None
