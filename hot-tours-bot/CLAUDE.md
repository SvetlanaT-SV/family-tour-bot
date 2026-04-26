# Family Tour Bot — карта проекта

Турагентство Pegas Touristik (Уфа). Бот публикует горящие туры в три канала, собирает заявки клиентов.

## Стек
- Python 3.11, python-telegram-bot 21.9
- Хостинг: Railway (auto-deploy из GitHub `SvetlanaT-SV/family-tour-bot`)
- AI: GigaChat (основной), Claude Anthropic (fallback), шаблон (последний fallback)
- Хранилище данных туров: Google Sheets (gspread)

## Каналы публикации
- **Telegram-канал** family_tour_channel — `Config.TELEGRAM_CHANNEL_ID`, HTML-разметка работает
- **VK-группа** family_toor (id=60704869) — `Config.VK_TOKEN` (групповой), HTML вырезается
- **MAX-канал** — `Config.MAX_TOKEN`, `Config.MAX_CHAT_ID`, поддерживает только `<b>` `<i>`

## Структура каталогов

```
main.py                — точка входа, запускает Telegram polling + JobQueue
config.py              — Config класс, читает все ENV vars; TELEGRAM_ADMIN_IDS — список через запятую
.env                   — локальные секреты (не в git); на Railway всё в Variables

ai/
  generator.py         — generate_post_from_dict() основная функция; пробует GigaChat → Claude → шаблон
  gigachat.py          — клиент Sber GigaChat API (OAuth + chat completions)

bot/
  handler.py           — публичный Telegram-бот для клиентов (5 вопросов) + админ-кнопки
                         publish_to_channels() — общий код публикации (используется и кнопкой, и планировщиком)
                         /max USER_ID text — команда админу для ответа клиенту MAX
  vk_handler.py        — VK Long Poll, диалог с клиентом в группе
  max_handler.py       — MAX polling, диалог + пересылка ответов админу

publisher/
  telegram.py          — TelegramPublisher (publish + notify_admin для всех админов)
  vk.py                — VKPublisher (только групповой токен, грузит фото через photos.getWallUploadServer)
  max.py               — MAXPublisher (REST API platform-api.max.ru)

sheets/
  client.py            — SheetsClient. Читает/пишет лист "Туры к публикации" и лист "Заявки".
                         Находит колонки ПО ИМЕНИ — не по индексу (insert column safe).

tourvisor/
  client.py            — клиент Tourvisor API. Используется ТОЛЬКО list.php (бесплатно):
                         find_country_id, list_hotels, find_hotel_id.
                         search.php и hotel.php требуют платную подписку — НЕ ИСПОЛЬЗУЕМ.

image_overlay/
  overlay.py           — Pillow, накладывает на фото красную плашку "ГОРЯЩИЙ ТУР",
                         название страны и цену. DejaVu Sans Bold (apt в Dockerfile).
```

## Ключевые потоки

### 1. Публикация тура (основной поток)
1. Менеджер заполняет строку в Google Sheets `Туры к публикации` (статус "НОВЫЙ")
2. `publish_from_sheets()` в main.py каждые 5 минут проверяет лист
3. Для НОВЫХ туров: вызов `generate_post_from_dict()` → GigaChat пишет текст
4. Скачивает фото → накладывает overlay → отправляет всем админам с тремя кнопками:
   - ✅ Сейчас → `_handle_approval` → `publish_to_channels` → TG/VK/MAX немедленно
   - ⏰ По расписанию → ставит в `SCHEDULED_POSTS` → ближайший слот 9/14/19 МСК
   - ❌ Пропустить
5. После одобрения статус в Sheets меняется на ОПУБЛИКОВАН

### 2. Заявка клиента
- Клиент пишет одному из ботов → 5 вопросов (имя, телефон с валидацией, даты, состав, бюджет)
- Сохранение в Sheets лист "Заявки" + Telegram-уведомление всем `TELEGRAM_ADMIN_IDS`

### 3. Сохранение между перезапусками Railway
- `pending_posts.json` — посты ожидающие одобрения
- `scheduled_posts.json` — посты в очереди расписания
- Railway filesystem ephemeral, поэтому на перезапуске может потеряться → handler.py восстанавливает текст из самого Telegram-сообщения через `msg.text_html` / `msg.caption_html`

## Колонки Google Sheets — лист "Туры к публикации"
Бот ищет ПО ИМЕНИ заголовка, можно переставлять, главное чтобы имена точно совпали:
`Статус | Страна | Курорт | Отель | Питание | Дата вылета | Ночей | Цена/чел | Город вылета | Особенности отеля | Фото URL | Ссылка | Опубликован | Ошибка`

## ENV vars (Railway Variables)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`
- `TELEGRAM_ADMIN_ID` — comma-separated `418012639,ID_МЕНЕДЖЕРА`
- `VK_TOKEN`, `VK_GROUP_ID=60704869` (только групповой токен, user token не нужен)
- `MAX_TOKEN`, `MAX_CHAT_ID`
- `GIGACHAT_AUTH_KEY` — Base64(client_id:secret) от Sber AI Studio, scope GIGACHAT_API_PERS
- `ANTHROPIC_API_KEY` — Claude (fallback, опциональный)
- `GOOGLE_CREDENTIALS_JSON`, `GOOGLE_SHEET_ID`
- `TOURVISOR_LOGIN`, `TOURVISOR_PASSWORD` — только справочники
- Фильтры: `MAX_PRICE`, `DAYS_AHEAD`, `NIGHTS_FROM`, `NIGHTS_TO`

## Что НЕЛЬЗЯ делать
- Упоминать в постах удобства которых нет в данных (SPA, аквапарк, анимация и т.п.) — обманывать подписчиков нельзя
- Использовать Tourvisor `search.php` или `hotel.php` — платная подписка
- Скрейпить pegast.ru — Pegas блокирует Railway IP
- Скрипить hooks (`--no-verify`) или `--force` push в main без согласования

## Полезные команды
```bash
git push origin main      # триггерит auto-deploy на Railway
```

Railway logs: Deployments → последний деплой → Deploy Logs, фильтр по ключевому слову.
