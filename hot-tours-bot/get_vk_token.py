"""
get_vk_token.py — Получение VK токена для загрузки фото

Запускать когда токен в .env устарел (раз в 24 часа).
Скрипт запрашивает токен напрямую из Python — токен привязывается
к IP этого компьютера, а не к IP браузера.

Запуск:
    python get_vk_token.py
"""

import os
import re
import sys
import requests
from dotenv import load_dotenv

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_env_path)

# Kate Mobile — приложение VK для Android.
# Его токены не привязаны к конкретному IP-адресу браузера.
CLIENT_ID     = "2685278"
CLIENT_SECRET = "lxhD8OD7dMsqtXIm5IUY"


def get_proxies() -> dict:
    """Возвращает прокси из VK_PROXY (нужно чтобы токен привязался к тому же IP что и бот)."""
    proxy = os.getenv("VK_PROXY", "").strip()
    if proxy:
        return {"http": proxy, "https": proxy}
    return {}


def get_token(username: str, password: str) -> str:
    """Получает user token через Kate Mobile API (через прокси из VK_PROXY)."""
    proxies = get_proxies()
    if proxies:
        print(f"  Используем прокси: {os.getenv('VK_PROXY')}")
    resp = requests.post(
        "https://oauth.vk.com/token",
        data={
            "grant_type":    "password",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username":      username,
            "password":      password,
            "scope":         "wall,photos,groups",
            "v":             "5.199",
        },
        proxies=proxies or None,
        timeout=15,
    )
    data = resp.json()
    if "access_token" in data:
        return data["access_token"]
    error = data.get("error_description") or data.get("error") or str(data)
    raise RuntimeError(f"Ошибка VK OAuth: {error}")


def update_env(token: str, env_path: str = ".env") -> None:
    """Обновляет VK_USER_TOKEN в файле .env."""
    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = r"^VK_USER_TOKEN=.*$"
    new_line = f"VK_USER_TOKEN={token}"

    if re.search(pattern, content, flags=re.MULTILINE):
        content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
    else:
        # Добавляем после VK_TOKEN
        content = re.sub(
            r"(^VK_TOKEN=.*$)",
            r"\1\n" + new_line,
            content,
            flags=re.MULTILINE,
        )

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ VK_USER_TOKEN обновлён в {env_path}")


def main():
    print("=" * 55)
    print("  Получение VK User Token для загрузки фото")
    print("=" * 55)
    print()

    # Читаем логин/пароль из .env или запрашиваем вручную
    username = os.getenv("VK_LOGIN", "").strip()
    password = os.getenv("VK_PASSWORD", "").strip()

    if not username:
        username = input("Введите email/телефон от аккаунта VK: ").strip()
    if not password:
        import getpass
        password = getpass.getpass("Введите пароль VK: ")

    if not username or not password:
        print("❌ Логин и пароль обязательны")
        sys.exit(1)

    print(f"\n🔑 Запрашиваем токен для: {username}")

    try:
        token = get_token(username, password)
        print(f"✅ Токен получен: {token[:30]}...")

        env_path = os.path.join(os.path.dirname(__file__), ".env")
        update_env(token, env_path)

        print()
        print("Теперь перезапустите бота: python main.py")
        print()
        print("⚠️  Токен действует 24 часа.")
        print("   Если фото снова перестанут публиковаться — запустите скрипт снова.")

    except RuntimeError as e:
        print(f"\n❌ {e}")
        print()
        print("Возможные причины:")
        print("  1. Неверный логин или пароль")
        print("  2. VK заблокировал вход — проверьте почту/СМС от VK")
        print("  3. На аккаунте включена двухфакторная аутентификация")
        sys.exit(1)


if __name__ == "__main__":
    main()
