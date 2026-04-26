# Family Tour Bot — карта проекта

Турагентство Pegas Touristik (Уфа, опыт 12+ лет). Бот публикует горящие туры и новости туристической индустрии в три канала, собирает заявки клиентов.

## Стек
- Python 3.11, python-telegram-bot 21.9
- Хостинг: Railway (auto-deploy из GitHub `SvetlanaT-SV/family-tour-bot`)
- AI: GigaChat (основной), Claude Anthropic (fallback), шаблон (последний fallback)
- Хранилище данных: Google Sheets (gspread) — лист "Туры к публикации", "Расписание", "Источники новостей", "Заявки"
- Скрейпинг новостей: BeautifulSoup4 на публичных Telegram-каналах (t.me/s/{name})
- Картинки: Pillow + DejaVu Sans Bold (для overlay на фото туров)

## Каналы публикации
- **Telegram-канал** family_tour_channel — `Config.TELEGRAM_CHANNEL_ID`, HTML-разметка работает
- **VK-группа** family_toor (id=60704869) — `Config.VK_TOKEN` (групповой токен), HTML вырезается
- **MAX-канал** — `Config.MAX_TOKEN`, `Config.MAX_CHAT_ID`, поддерживает только `<b>` `<i>`

## Структура каталогов

```
main.py                — точка входа, запускает Telegram polling + JobQueue.
                         Джобы:
                         · publish_from_sheets    — каждые 5 мин, читает "Туры к публикации"
                         · search_tourvisor_tours — каждые 4 ч (отключён, Tourvisor поиск платный)
                         · check_scheduled_posts  — каждую минуту, читает лист "Расписание"
                         · collect_news_job       — ежедневно 08:00 МСК
config.py              — Config класс, читает все ENV vars; TELEGRAM_ADMIN_IDS — список через запятую
.env                   — локальные секреты (не в git); на Railway всё в Variables

ai/
  generator.py         — generate_post_from_dict() основная функция; пробует GigaChat → Claude → шаблон.
                         _sanitize_html_for_telegram() — чистит <br>/<p>/<div> и markdown ** ** перед отправкой.
                         COUNTRY_PREPS — словарь "куда?/где?" для 50+ стран (в Турцию/Турции, на Мальдивы/Мальдивах).
                         CITY_GENITIVE — родительный падеж 20+ городов вылета.
  gigachat.py          — клиент Sber GigaChat API (OAuth + chat completions, кэш токена 30 мин)

news/
  collector.py         — fetch_channel_posts(channel, since_dt). Скрейпит публичный
                         t.me/s/{username} (без авторизации, BeautifulSoup).
                         Возвращает посты с date/text/photo_url/views/post_url.
  processor.py         — select_and_rewrite(posts, top_n=3). Передаёт посты в GigaChat
                         с промптом-инструкцией (clickbait-заголовок в <b>, наш стиль),
                         парсит JSON-ответ.

bot/
  handler.py           — публичный Telegram-бот для клиентов (5 вопросов с валидацией телефона)
                         + админ-кнопки. Команды:
                         · /max USER_ID text — ответ клиенту MAX через бот
                         · /news             — ручной запуск сбора новостей
                         publish_to_channels() — общий код публикации (TG + VK + MAX),
                         используется и кнопкой ✅ Сейчас, и планировщиком расписания.
                         _next_schedule_slot() — ближайший слот из Config.PUBLISH_HOURS МСК.
                         _sanitize_html_for_telegram() — защита от не-Telegram HTML.
  vk_handler.py        — VK Long Poll, диалог с клиентом в группе
  max_handler.py       — MAX polling, диалог + пересылка ответов админу через /max

publisher/
  telegram.py          — TelegramPublisher (publish + notify_admin для всех админов)
  vk.py                — VKPublisher (только групповой токен, грузит фото через
                         photos.getWallUploadServer + photos.saveWallPhoto)
  max.py               — MAXPublisher (REST API platform-api.max.ru)

sheets/
  client.py            — SheetsClient. Все колонки ищутся ПО ИМЕНИ в первой строке
                         (insert column safe — можно вставлять колонки куда угодно).
                         Листы: "Заявки", "Туры к публикации", "Расписание",
                         "Источники новостей".

tourvisor/
  client.py            — клиент Tourvisor API. Используется ТОЛЬКО list.php (бесплатно):
                         find_country_id, list_hotels, find_hotel_id.
                         search.php и hotel.php требуют платную подписку — НЕ ИСПОЛЬЗУЕМ.

image_overlay/
  overlay.py           — Pillow, накладывает на фото красную плашку "ГОРЯЩИЙ ТУР",
                         название страны и цену. DejaVu Sans Bold (apt-устанавливается
                         в Dockerfile). Применяется только для постов горящих туров.
```

## Ключевые потоки

### 1. Публикация тура (основной поток)
1. Менеджер заполняет строку в Google Sheets `Туры к публикации` (статус "НОВЫЙ")
2. `publish_from_sheets()` в main.py каждые 5 минут проверяет лист
3. Для НОВЫХ туров: вызов `generate_post_from_dict()` → GigaChat пишет текст с учётом данных и колонки "Особенности отеля"
4. Скачивает фото → накладывает overlay → отправляет всем админам с тремя кнопками:
   - ✅ Сейчас → `_handle_approval` → `publish_to_channels` → TG/VK/MAX немедленно
   - ⏰ По расписанию → запись в Sheets лист "Расписание" → check_scheduled_posts публикует в ближайший слот 9/14/19 МСК
   - ❌ Пропустить
5. После одобрения статус в Sheets меняется на ОПУБЛИКОВАН

### 2. Сбор новостей (Stage 2)
1. Ежедневно в 08:00 МСК: `collect_news_job()` читает лист "Источники новостей" (только активные каналы)
2. Из каждого канала забирает посты за последние 24 часа через `fetch_channel_posts()`
3. Все посты передаются в `select_and_rewrite()` → GigaChat выбирает топ-3 и переписывает в наш стиль
4. Каждая новость отправляется админам как превью с теми же кнопками ✅/⏰/❌
5. В превью есть строка "🔗 Источник: @канал" — для проверки, в самом посте при публикации её НЕТ
6. Команда `/news` запускает сбор вручную не дожидаясь утра

### 3. Заявка клиента
- Клиент пишет одному из ботов → 5 вопросов (имя, телефон с валидацией, даты, состав, бюджет)
- Сохранение в Sheets лист "Заявки" + Telegram-уведомление всем `TELEGRAM_ADMIN_IDS`

### 4. Расписание публикаций
- Все запланированные посты идут в Google Sheets лист "Расписание" (а не локальный JSON)
- Это переживает перезапуски Railway (диск ephemeral) — раньше очередь терялась
- check_scheduled_posts каждую минуту читает Sheets, публикует те у которых наступило время, ставит "ОПУБЛИКОВАН"
- Слоты публикации: `Config.PUBLISH_HOURS = [9, 14, 19]` МСК

### 5. Восстановление после перезапуска
- `pending_posts.json` локально — пишется в файл, переживает короткие перезапуски
- На длинных передеплоях файл теряется → `_handle_approval` восстанавливает текст и фото из самого Telegram-сообщения (`msg.text_html`, `msg.photo`)
- Защита от двойной публикации — проверка маркера "ОПУБЛИКОВАНО"/"ПРОПУЩЕН" в caption сообщения

## Колонки Google Sheets

### Лист "Туры к публикации"
Все ищутся по имени, можно переставлять:
`Статус | Страна | Курорт | Отель | Питание | Дата вылета | Ночей | Цена/чел | Город вылета | Особенности отеля | Фото URL | Ссылка | Опубликован | Ошибка`

- **Город вылета** — Уфа (по умолчанию), Казань, Москва и т.д. Бот склоняет в родительный.
- **Особенности отеля** — свободный текст реальных удобств ("первая линия, аквапарк, ультра все включено"). GigaChat использует ТОЛЬКО эти данные, не выдумывает.

### Лист "Расписание"
`Когда | Когда МСК | tour_id | Статус | Страна | Цена | Дата вылета | Текст | Photo URL | Photo bytes | Overlay страна | Overlay цена | Overlay вылет`
Статусы: ОЖИДАЕТ → ОПУБЛИКОВАН (или ОТМЕНЁН).

### Лист "Источники новостей"
`Канал | Категория | Активен`
- **Канал** — `https://t.me/atorus_news` или просто `atorus_news`
- **Активен** — `да` или `нет` (только активные обрабатываются)
- Только публичные каналы — Bot API не имеет доступа к закрытым

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

## Команды бота для админа
В чате с `@hottourpegas_bot`:
- `/max USER_ID текст` — отправить сообщение клиенту MAX через бот
- `/news` — запустить сбор новостей вручную (не дожидаясь 08:00)

## HTML и стиль постов
- Telegram parse_mode=HTML принимает только: `<b>`, `<i>`, `<u>`, `<s>`, `<a>`, `<code>`, `<pre>`, `<blockquote>`, `<tg-spoiler>`, `<span>`
- `_sanitize_html_for_telegram()` ПРИНУДИТЕЛЬНО:
  - заменяет `<br>` / `<p>` / `<hr>` на реальные переносы строк
  - конвертирует markdown `**жирный**` → `<b>жирный</b>` и `*курсив*` → `<i>курсив</i>`
  - вырезает все остальные неподдерживаемые теги
- Применяется к выходу GigaChat и Claude перед отправкой

## Что НЕЛЬЗЯ делать
- Упоминать в постах удобства которых нет в данных (SPA, аквапарк, анимация и т.п.) — обманывать подписчиков недопустимо. Если есть колонка "Особенности отеля" — использовать ТОЛЬКО её, не выдумывать.
- Использовать Tourvisor `search.php` или `hotel.php` — платная подписка, аккаунт не оплачен.
- Скрейпить pegast.ru — Pegas блокирует Railway IP.
- Скипить хуки (`--no-verify`) или делать `--force push` в main без явного согласования.
- В постах новостей упоминать названия каналов-источников. Источник виден только в превью админу.

## Полезные команды
```bash
git push origin main      # триггерит auto-deploy на Railway
```

Railway logs: Deployments → активный (зелёный) деплой → View logs → Deploy Logs.
Полезные фильтры: `Sheets`, `VK`, `GigaChat`, `Новости`, `scheduled`, `Не удалось отправить`.
