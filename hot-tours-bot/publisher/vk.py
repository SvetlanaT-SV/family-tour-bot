"""
publisher/vk.py — Публикация постов в ВКонтакте

Публикует пост с фото на стену группы vk.com/family_toor.

Два токена:
  VK_TOKEN      — токен группы (для публикации постов)
  VK_USER_TOKEN — токен пользователя-администратора (для загрузки фото)

Как получить VK_USER_TOKEN:
  1. Создай Standalone приложение на vk.com/dev
  2. Открой в браузере:
     https://oauth.vk.com/authorize?client_id=APP_ID&display=page
     &redirect_uri=https://oauth.vk.com/blank.html&scope=wall,photos,groups
     &response_type=token&v=5.199
  3. Авторизуйся — скопируй access_token из URL
  Внимание: токен действует 24 часа, нужно обновлять.

Документация VK API: https://dev.vk.com/api/wall.post
"""

import logging
import os
import re
import requests
from typing import Optional

from config import Config

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

    def __init__(self, token: str, group_id: int):
        self.token    = token
        self.group_id = group_id

    @staticmethod
    def _proxies() -> Optional[dict]:
        """Возвращает прокси из VK_PROXY если задан (нужен для user токена — IP должен совпадать с браузером)."""
        proxy = os.getenv("VK_PROXY", "").strip()
        if proxy:
            return {"http": proxy, "https": proxy}
        return None

    def _call(self, method: str, params: dict, token: str = None, use_proxy: bool = False) -> dict:
        """Делает запрос к VK API. Если token не указан — использует self.token."""
        url = self.API_URL.format(method=method)
        all_params = {
            "access_token": token or self.token,
            "v":            self.API_VER,
            **params,
        }
        try:
            resp = requests.get(url, params=all_params, timeout=30,
                                proxies=self._proxies() if use_proxy else None)
            result = resp.json()
            if "error" in result:
                err = result["error"]
                logger.warning(f"VK API ошибка {err.get('error_code')}: {err.get('error_msg')}")
            return result
        except Exception as e:
            logger.warning(f"Ошибка запроса к VK: {e}")
            return {}

    def _refresh_user_token(self) -> Optional[str]:
        """
        Пробует обновить VK_USER_TOKEN через Kate Mobile API.
        Работает только если заданы VK_LOGIN и VK_PASSWORD в .env.
        """
        login    = os.getenv("VK_LOGIN", "").strip()
        password = os.getenv("VK_PASSWORD", "").strip()
        if not login or not password:
            return None

        logger.info("VK: обновляем токен через VK_LOGIN/VK_PASSWORD...")
        try:
            resp = requests.post(
                "https://oauth.vk.com/token",
                data={
                    "grant_type":    "password",
                    "client_id":     "2685278",
                    "client_secret": "lxhD8OD7dMsqtXIm5IUY",
                    "username":      login,
                    "password":      password,
                    "scope":         "wall,photos,groups",
                    "v":             "5.199",
                },
                timeout=15,
            )
            data = resp.json()
            token = data.get("access_token")
            if not token:
                logger.warning(f"VK: не удалось обновить токен: {data}")
                return None

            # Обновляем в .env и в переменных окружения
            env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
            env_path = os.path.normpath(env_path)
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    content = f.read()
                new_line = f"VK_USER_TOKEN={token}"
                if re.search(r"^VK_USER_TOKEN=", content, re.MULTILINE):
                    content = re.sub(r"^VK_USER_TOKEN=.*$", new_line, content, flags=re.MULTILINE)
                else:
                    content += f"\n{new_line}\n"
                with open(env_path, "w", encoding="utf-8") as f:
                    f.write(content)
            os.environ["VK_USER_TOKEN"] = token
            logger.info("VK: токен обновлён автоматически")
            return token
        except Exception as e:
            logger.warning(f"VK: ошибка автообновления токена: {e}")
            return None

    def _upload_photo(self, photo_url: str) -> Optional[str]:
        """
        Загружает фото на серверы ВК через пользовательский токен.
        Возвращает attachment-строку вида "photo{owner_id}_{photo_id}".
        """
        user_token = os.getenv("VK_USER_TOKEN") or Config.VK_USER_TOKEN
        if not user_token:
            logger.warning("VK: VK_USER_TOKEN не задан, пост будет без фото")
            return None

        proxy = self._proxies()
        if proxy:
            logger.info(f"VK: используем прокси {os.getenv('VK_PROXY')} для загрузки фото")

        # Шаг 1: получаем адрес сервера (через user token — группа в параметрах)
        upload_data = self._call(
            "photos.getWallUploadServer",
            {"group_id": self.group_id},
            token=user_token,
            use_proxy=True,
        )

        # Если ошибка авторизации (истёк токен, другой IP) — пробуем обновить и повторить
        err = upload_data.get("error", {})
        if err.get("error_code") in (5, 1117) or "ip address" in str(err.get("error_msg", "")).lower():
            logger.warning("VK: токен привязан к другому IP, пробуем обновить...")
            new_token = self._refresh_user_token()
            if new_token:
                user_token = new_token
                upload_data = self._call(
                    "photos.getWallUploadServer",
                    {"group_id": self.group_id},
                    token=user_token,
                    use_proxy=True,
                )

        upload_url = upload_data.get("response", {}).get("upload_url")
        if not upload_url:
            logger.warning("VK: не получили upload_url, пост будет без фото")
            return None

        # Шаг 2: скачиваем фото по внешней ссылке
        try:
            img_response = requests.get(photo_url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0"
            })
            img_response.raise_for_status()
        except Exception as e:
            logger.warning(f"VK: не удалось скачать фото: {e}")
            return None

        # Шаг 3: загружаем на сервер ВК
        try:
            upload_response = requests.post(
                upload_url,
                files={"photo": ("photo.jpg", img_response.content, "image/jpeg")},
                timeout=30,
            ).json()
        except Exception as e:
            logger.warning(f"VK: не удалось загрузить фото: {e}")
            return None

        # Шаг 4: сохраняем фото в альбом группы
        save_data = self._call(
            "photos.saveWallPhoto",
            {
                "group_id": self.group_id,
                "photo":    upload_response.get("photo"),
                "server":   upload_response.get("server"),
                "hash":     upload_response.get("hash"),
            },
            token=user_token,
            use_proxy=True,
        )

        photos = save_data.get("response", [])
        if photos:
            photo = photos[0]
            attachment = f"photo{photo['owner_id']}_{photo['id']}"
            logger.info(f"VK: фото загружено ({attachment})")
            return attachment

        logger.warning("VK: не удалось сохранить фото")
        return None

    def publish(self, text: str, photo_url: Optional[str] = None) -> Optional[int]:
        """Публикует пост на стене группы. Возвращает post_id."""
        # Заменяем ссылку на бота
        vk_text = text.replace(
            "📩 Написать нам: <b>@hottourpegas_bot</b>",
            "📩 Написать нам: vk.me/family_toor",
        )
        # Убираем HTML-теги — ВКонтакте их не поддерживает в постах
        vk_text = re.sub(r"<[^>]+>", "", vk_text)

        params = {
            "owner_id":   f"-{self.group_id}",
            "from_group": 1,
            "message":    vk_text,
        }

        # Загружаем фото через user token
        if photo_url:
            attachment = self._upload_photo(photo_url)
            if attachment:
                params["attachments"] = attachment
            else:
                # Если фото не загрузилось — добавляем ссылку в текст
                params["message"] = f"{vk_text}\n\n{photo_url}"

        result = self._call("wall.post", params)
        post_id = result.get("response", {}).get("post_id")

        if post_id:
            logger.info(f"VK: опубликовано (post_id={post_id})")
            return post_id

        logger.warning(f"VK: post_id не получен, ответ API: {result}")
        return None
