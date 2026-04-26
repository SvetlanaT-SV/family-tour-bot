"""Сбор и обработка новостей из туристических Telegram-каналов."""
from .collector import fetch_channel_posts, parse_channel_url
from .processor import select_and_rewrite
