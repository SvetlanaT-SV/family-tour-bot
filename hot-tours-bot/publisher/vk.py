"""
publisher/vk.py — Публикация постов в ВКонтакте

Публикует пост с фото на стену группы vk.com/family_toor.

Токены:
- VK_TOKEN (групповой) — для wall.post (пост от имени группы)
- VK_USER_TOKEN (личный, опционально) — для загрузки фото
  (photos.getWallUploadServer и photos.saveWallPhoto не работают
  с групповым токеном — VK возвращает ошибку 27)

Если VK_USER_TOKEN не задан, фото будет приложено ссылкой (как fallback).

Документация VK API: https://dev.vk.com/api/wall.post
"""

import logging
import re
import requests
from typing import Optional

logger = logging.getLogger(__name__)


class VKPublisher:
    """
    Публикует посты в группу ВКонтакте.

    Пример:
        vk = VKPublisher(token="vk1.a...", group_id=123456789)
        vk.publish("Горящий тур!", photo_url="https://...")
    """

    API_URL = "https://api.vk.com/method/{method}"
    API_VER = "5.199"

    def __init__(self, token: str, group_id: int, user_token: Optional[str] = None):
        self.token      = token        # групповой токен (для wall.post)
        self.user_token = user_token   # личный токен админа группы (для photo upload)
        self.group_id   = group_id

    def _call(self, method: str, params: dict, use_user_token: bool = False) -> dict:
        """
        Делает запрос к VK API. По умолчанию использует групповой токен,
        но для методов которые требуют админ-прав (photos.*) можно
        передать use_user_token=True — тогда используется VK_USER_TOKEN.
        """
        url = self.API_URL.format(method=method)
        token = self.user_token if (use_user_token and self.user_token) else self.token
        all_params = {
            "access_token": token,
            "v":            self.API_VER,
            **params,
        }
        try:
            resp = requests.get(url, params=all_params, timeout=30)
            result = resp.json()
            if "error" in result:
                err = result["error"]
                logger.warning(f"VK API ошибка {err.get('error_code')}: {err.get('error_msg')}")
            return result
        except Exception as e:
            logger.warning(f"VK: ошибка запроса: {e}")
            return {}

    def _upload_photo(self, photo_url: Optional[str] = None,
                      photo_bytes: Optional[bytes] = None) -> Optional[str]:
        """
        Загружает фото на стену группы через групповой токен.
        Принимает либо URL, либо готовые bytes. photo_bytes имеет приоритет.
        Возвращает attachment-строку вида "photo{owner_id}_{photo_id}".
        """
        # photos.getWallUploadServer работает ТОЛЬКО с user-token (group-token падает с error 27)
        upload_data = self._call(
            "photos.getWallUploadServer",
            {"group_id": self.group_id},
            use_user_token=True,
        )
        upload_url_vk = upload_data.get("response", {}).get("upload_url")
        if not upload_url_vk:
            logger.warning(f"VK: upload_url не получен, ответ={upload_data}")
            return None

        # Получаем содержимое фото
        if photo_bytes:
            img_content = photo_bytes
        elif photo_url:
            try:
                img_response = requests.get(photo_url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0"
                })
                img_response.raise_for_status()
                img_content = img_response.content
            except Exception as e:
                logger.warning(f"VK: не удалось скачать фото: {e}")
                return None
        else:
            return None

        # Загружаем на сервер ВК
        try:
            upload_response = requests.post(
                upload_url_vk,
                files={"photo": ("photo.jpg", img_content, "image/jpeg")},
                timeout=30,
            ).json()
        except Exception as e:
            logger.warning(f"VK: не удалось загрузить фото: {e}")
            return None

        # photos.saveWallPhoto тоже требует user-token
        save_data = self._call(
            "photos.saveWallPhoto",
            {
                "group_id": self.group_id,
                "photo":    upload_response.get("photo"),
                "server":   upload_response.get("server"),
                "hash":     upload_response.get("hash"),
            },
            use_user_token=True,
        )

        photos = save_data.get("response", [])
        if photos:
            photo = photos[0]
            attachment = f"photo{photo['owner_id']}_{photo['id']}"
            logger.info(f"VK: фото загружено ({attachment})")
            return attachment

        logger.warning(f"VK: не удалось сохранить фото, ответ={save_data}")
        return None

    def publish(self, text: str, photo_url: Optional[str] = None,
                photo_bytes: Optional[bytes] = None) -> Optional[int]:
        """Публикует пост на стене группы. Возвращает post_id."""
        # Заменяем любое упоминание @hottourpegas_bot на ссылку VK-сообщества
        vk_text = re.sub(
            r"@hottourpegas_bot",
            "vk.me/family_toor",
            text,
            flags=re.IGNORECASE,
        )
        vk_text = re.sub(r"<[^>]+>", "", vk_text)

        params = {
            "owner_id":   f"-{self.group_id}",
            "from_group": 1,
            "message":    vk_text,
        }

        if photo_url or photo_bytes:
            attachment = self._upload_photo(photo_url=photo_url, photo_bytes=photo_bytes)
            if attachment:
                params["attachments"] = attachment
            elif photo_url:
                params["message"] = f"{vk_text}\n\n{photo_url}"

        result = self._call("wall.post", params)
        post_id = result.get("response", {}).get("post_id")

        if post_id:
            logger.info(f"VK: опубликовано (post_id={post_id})")
            return post_id

        logger.warning(f"VK: post_id не получен, ответ API: {result}")
        return None
