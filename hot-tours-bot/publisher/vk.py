"""
publisher/vk.py — Публикация постов в ВКонтакте

Публикует пост с фото на стену группы vk.com/family_toor.

Как получить VK_TOKEN:
  1. Зайди на vk.com/dev → Мои приложения → Создать приложение
  2. Тип: Standalone
  3. В настройках: Права → wall, photos, groups
  4. Получи токен через vkhost.github.io/apps/ или vk.com/dev/direct_auth

Документация VK API: https://dev.vk.com/api/wall.post
"""

import requests
from typing import Optional


class VKPublisher:
    """
    Публикует посты в группу ВКонтакте.

    Пример:
        vk = VKPublisher(token="vk1.a...", group_id=123456789)
        vk.publish("Горящий тур!", photo_url="https://...")
    """

    API_URL   = "https://api.vk.com/method/{method}"
    API_VER   = "5.199"   # версия API ВК

    def __init__(self, token: str, group_id: int):
        """
        token    — токен доступа к группе (из настроек приложения ВК)
        group_id — числовой ID группы (без минуса)
        """
        self.token    = token
        self.group_id = group_id

    def _call(self, method: str, params: dict) -> dict:
        """Делает запрос к VK API"""
        url = self.API_URL.format(method=method)
        all_params = {
            "access_token": self.token,
            "v":            self.API_VER,
            **params,
        }
        try:
            resp = requests.get(url, params=all_params, timeout=30)
            result = resp.json()
            if "error" in result:
                err = result["error"]
                print(f"⚠️  VK API ошибка {err.get('error_code')}: {err.get('error_msg')}")
            return result
        except Exception as e:
            print(f"❌ Ошибка запроса к VK: {e}")
            return {}

    def _upload_photo(self, photo_url: str) -> Optional[str]:
        """
        Загружает фото по ссылке на серверы ВК.
        ВК не принимает внешние ссылки напрямую — нужно сначала загрузить.

        Возвращает attachment-строку вида "photo-123_456"
        """
        # Шаг 1: получаем адрес сервера для загрузки фото
        upload_data = self._call("photos.getWallUploadServer", {
            "group_id": self.group_id,
        })
        upload_url = upload_data.get("response", {}).get("upload_url")
        if not upload_url:
            print("⚠️  VK: не получили upload_url, пост будет без фото")
            return None

        # Шаг 2: скачиваем фото по ссылке
        try:
            img_response = requests.get(photo_url, timeout=15)
            img_response.raise_for_status()
        except Exception as e:
            print(f"⚠️  VK: не удалось скачать фото: {e}")
            return None

        # Шаг 3: загружаем на сервер ВК
        try:
            upload_response = requests.post(
                upload_url,
                files={"photo": ("photo.jpg", img_response.content, "image/jpeg")},
                timeout=30,
            ).json()
        except Exception as e:
            print(f"⚠️  VK: не удалось загрузить фото: {e}")
            return None

        # Шаг 4: сохраняем фото в альбом группы
        save_data = self._call("photos.saveWallPhoto", {
            "group_id": self.group_id,
            "photo":    upload_response.get("photo"),
            "server":   upload_response.get("server"),
            "hash":     upload_response.get("hash"),
        })

        photos = save_data.get("response", [])
        if photos:
            photo = photos[0]
            # attachment-строка: "photo{owner_id}_{photo_id}"
            attachment = f"photo{photo['owner_id']}_{photo['id']}"
            print(f"✅ VK: фото загружено ({attachment})")
            return attachment

        return None

    def publish(self, text: str, photo_url: Optional[str] = None) -> Optional[int]:
        """
        Публикует пост на стене группы.

        Возвращает post_id опубликованного поста.
        """
        params = {
            "owner_id":   f"-{self.group_id}",  # минус = это группа
            "from_group": 1,                      # от имени группы, не пользователя
            "message":    text,
        }

        # Если есть фото — сначала загружаем
        if photo_url:
            attachment = self._upload_photo(photo_url)
            if attachment:
                params["attachments"] = attachment

        result = self._call("wall.post", params)
        post_id = result.get("response", {}).get("post_id")

        if post_id:
            print(f"✅ VK: опубликовано (post_id={post_id})")
            print(f"   Ссылка: https://vk.com/wall-{self.group_id}_{post_id}")
            return post_id

        return None
