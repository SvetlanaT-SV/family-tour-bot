"""
news/collector.py — скрейпинг публичных Telegram-каналов через t.me/s/{name}.

Бот не подписан на каналы (Bot API этого не позволяет), вместо этого
читаем публичную HTML-версию канала: https://t.me/s/{username}.
Возвращается последние ~20 постов без авторизации.
"""

import logging
import re
import requests
from datetime import datetime, timezone
from typing import Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def parse_channel_url(url_or_name: str) -> Optional[str]:
    """
    Из любой формы (полная ссылка, @name, просто name) делает username канала.
    """
    if not url_or_name:
        return None
    s = url_or_name.strip().lstrip("@")
    # https://t.me/atorus_news, https://t.me/s/atorus_news, t.me/atorus_news
    m = re.search(r"t\.me/(?:s/)?([a-zA-Z0-9_]+)", s)
    if m:
        return m.group(1)
    # Просто 'atorus_news'
    if re.match(r"^[a-zA-Z0-9_]+$", s):
        return s
    return None


def fetch_channel_posts(channel: str, since_dt: Optional[datetime] = None,
                        limit: int = 20) -> list[dict]:
    """
    Возвращает посты канала за период since_dt..сейчас.
    Если since_dt не задан — последние limit постов.

    Каждый пост: {date, text, photo_url, views, post_url, channel_username}
    """
    username = parse_channel_url(channel)
    if not username:
        logger.warning(f"Новости: некорректная ссылка на канал: {channel!r}")
        return []

    url = f"https://t.me/s/{username}"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Новости: не удалось получить {url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    posts = []

    for msg in soup.select(".tgme_widget_message"):
        # Дата (атрибут datetime у <time>)
        time_tag = msg.select_one("time[datetime]")
        if not time_tag:
            continue
        try:
            dt = datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
        except Exception:
            continue

        if since_dt and dt < since_dt:
            continue

        # Текст
        text_el = msg.select_one(".tgme_widget_message_text")
        text = text_el.get_text("\n", strip=True) if text_el else ""

        # Фото ищем в нескольких возможных местах:
        #   1. одиночное фото                — .tgme_widget_message_photo_wrap
        #   2. альбом из нескольких фото     — .tgme_widget_message_grouped_layer .tgme_widget_message_photo_wrap
        #   3. превью ссылки (статья и т.п.) — .link_preview_right_image / .link_preview_image
        #   4. превью видео                  — .tgme_widget_message_video_thumb
        photo_url = ""
        candidates = []
        for sel in (
            ".tgme_widget_message_photo_wrap",
            ".tgme_widget_message_grouped_layer .tgme_widget_message_photo_wrap",
            ".link_preview_right_image",
            ".link_preview_image",
            ".tgme_widget_message_video_thumb",
        ):
            el = msg.select_one(sel)
            if el is not None:
                candidates.append(el)
        for el in candidates:
            style = el.attrs.get("style", "")
            m = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", style)
            if m:
                photo_url = m.group(1)
                break

        # Просмотры
        views = 0
        views_el = msg.select_one(".tgme_widget_message_views")
        if views_el:
            v = views_el.get_text(strip=True).replace(" ", "").replace(",", ".").upper()
            if v.endswith("K"):
                try: views = int(float(v[:-1]) * 1000)
                except: pass
            elif v.endswith("M"):
                try: views = int(float(v[:-1]) * 1_000_000)
                except: pass
            else:
                try: views = int(re.sub(r"[^\d]", "", v) or 0)
                except: pass

        # Ссылка на пост
        post_url = ""
        link_el = msg.select_one(".tgme_widget_message_date")
        if link_el and link_el.has_attr("href"):
            post_url = link_el["href"]

        if text or photo_url:
            posts.append({
                "date":             dt,
                "text":             text,
                "photo_url":        photo_url,
                "views":            views,
                "post_url":         post_url,
                "channel_username": username,
            })

    # Сортируем по дате убывание
    posts.sort(key=lambda p: p["date"], reverse=True)
    if not since_dt:
        posts = posts[:limit]
    with_photo = sum(1 for p in posts if p.get("photo_url"))
    logger.info(
        f"Новости: канал @{username} — {len(posts)} постов "
        f"({with_photo} с фото, {len(posts) - with_photo} без)"
    )
    return posts
