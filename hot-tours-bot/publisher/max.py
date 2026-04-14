"""
publisher/max.py — Публикация постов в MAX (мессенджер от VK)

Публикует пост с фото в канал MAX.

Как получить токен бота:
  1. Открой MAX → найди @PrimeBot (официальный бот для создания ботов)
  2. Создай нового бота, получи токен
  3. Добавь бота в свой канал как администратора с правом публикации

Как найти chat_id канала:
  1. Добавь бота в канал
  2. Сделай GET https://platform-api.max.ru/chats
     с заголовком Authorization: <твой_токен>
  3. В ответе найди свой канал и скопируй chat_id

Документация MAX API: https://dev.max.ru/docs-api
"""

import requests
from typing import Optional


class MAXPublisher:
    """
    Публикует посты в канал MAX.

    Пример:
        max_pub = MAXPublisher(token="ваш_токен", chat_id=123456789)
        max_pub.publish("Горящий тур!", photo_url="https://...")
    """

    API_URL = "https://platform-api.max.ru"

    def __init__(self, token: str, chat_id: int):
        """
        token   — токен бота из @PrimeBot
        chat_id — числовой ID канала (получить через GET /chats)
        """
        self.chat_id = chat_id
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": token,
        })

    def _call(self, method: str, endpoint: str, **kwargs) -> dict:
        """Делает запрос к MAX API"""
        url = f"{self.API_URL}{endpoint}"
        try:
            resp = self.session.request(method, url, timeout=30, **kwargs)
            result = resp.json()
            if resp.status_code >= 400:
                print(f"⚠️  MAX API ошибка {resp.status_code}: {result}")
            return result
        except Exception as e:
            print(f"❌ Ошибка запроса к MAX: {e}")
            return {}

    def _upload_photo(self, photo_url: str) -> Optional[str]:
        """
        Загружает фото в MAX в два шага:
          1. Получаем URL для загрузки через POST /uploads?type=image
          2. Загружаем файл по этому URL через multipart/form-data
        Возвращает URL загруженного фото для использования в вложении.
        """
        # Шаг 1: получаем адрес сервера для загрузки
        upload_data = self._call("POST", "/uploads", params={"type": "image"})
        upload_url = upload_data.get("url")
        if not upload_url:
            print("⚠️  MAX: не получили upload URL, пост будет без фото")
            return None

        # Шаг 2: скачиваем фото по внешней ссылке
        try:
            img_resp = requests.get(photo_url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0"
            })
            img_resp.raise_for_status()
        except Exception as e:
            print(f"⚠️  MAX: не удалось скачать фото: {e}")
            return None

        # Шаг 3: загружаем на сервер MAX
        try:
            upload_resp = requests.post(
                upload_url,
                files={"file": ("photo.jpg", img_resp.content, "image/jpeg")},
                timeout=30,
            )
            upload_resp.raise_for_status()
            print(f"✅ MAX: фото загружено")
            return upload_url  # URL используется и как адрес загрузки, и как payload
        except Exception as e:
            print(f"⚠️  MAX: не удалось загрузить фото: {e}")
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

        body: dict = {
            "text": max_text[:4000],  # лимит MAX — 4000 символов
            "format": "html",
            "notify": True,
        }

        # Если есть фото — загружаем и добавляем как вложение
        if photo_url:
            uploaded_url = self._upload_photo(photo_url)
            if uploaded_url:
                body["attachments"] = [
                    {
                        "type": "image",
                        "payload": {"url": uploaded_url},
                    }
                ]

        result = self._call(
            "POST", "/messages",
            params={"chat_id": self.chat_id},
            json=body,
        )

        mid = result.get("message", {}).get("mid")
        if mid:
            print(f"✅ MAX: опубликовано (mid={mid})")
            return mid

        return None
