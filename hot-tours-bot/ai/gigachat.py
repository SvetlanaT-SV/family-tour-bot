"""
ai/gigachat.py — клиент GigaChat API от Сбера.

Авторизация двухступенчатая:
  1. POST /oauth с Basic-ключом → access_token (30 мин)
  2. POST /chat/completions с Bearer-токеном

Документация: https://developers.sber.ru/docs/ru/gigachat/api/overview
"""

import json
import logging
import os
import time
import uuid
import requests

logger = logging.getLogger(__name__)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL  = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

_token_cache: dict = {"value": None, "expires_at": 0}


def _get_access_token() -> str:
    """Возвращает Bearer-токен (использует кэш, обновляет за минуту до истечения)."""
    auth_key = os.getenv("GIGACHAT_AUTH_KEY", "").strip()
    if not auth_key:
        raise RuntimeError("GIGACHAT_AUTH_KEY не задан")

    now = time.time()
    if _token_cache["value"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["value"]

    scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip()
    headers = {
        "Authorization": f"Basic {auth_key}",
        "RqUID": str(uuid.uuid4()),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    resp = requests.post(
        OAUTH_URL,
        headers=headers,
        data={"scope": scope},
        timeout=20,
        verify=False,  # сертификаты Минцифры могут быть не установлены на Railway
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    expires_at = data.get("expires_at", 0) / 1000  # ms → s
    if not token:
        raise RuntimeError(f"GigaChat: токен не получен: {data}")

    _token_cache["value"] = token
    _token_cache["expires_at"] = expires_at or (now + 30 * 60)
    logger.info("GigaChat: получен новый access_token")
    return token


def generate(prompt: str, *, model: str = "GigaChat",
             temperature: float = 0.7, max_tokens: int = 600) -> str:
    """
    Отправляет prompt в GigaChat и возвращает текст ответа.
    model: GigaChat (Lite, бесплатно), GigaChat-Pro, GigaChat-Max.
    """
    token = _get_access_token()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "n": 1,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resp = requests.post(CHAT_URL, headers=headers, json=body, timeout=60, verify=False)
    if resp.status_code == 401:
        # Токен внезапно недействителен — сбросим кэш и повторим один раз
        _token_cache["value"] = None
        token = _get_access_token()
        headers["Authorization"] = f"Bearer {token}"
        resp = requests.post(CHAT_URL, headers=headers, json=body, timeout=60, verify=False)
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError(f"GigaChat: пустой ответ: {data}")
    return choices[0].get("message", {}).get("content", "").strip()


# Отключаем шумные предупреждения urllib3 о verify=False
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass
