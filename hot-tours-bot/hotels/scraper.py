"""
hotels/scraper.py — скрапер описаний отелей с tophotels.ru.

Алгоритм:
1. Поиск URL отеля через DuckDuckGo HTML-поиск (`site:tophotels.ru <название>`).
   Сам поиск на TopHotels — клиентский JS, обычным requests не парсится.
2. GET страницы /hotel/al<ID>/description — server-rendered HTML, обычный requests.
3. Парсинг BeautifulSoup'ом: тип отеля, расстояние до моря, рестораны,
   спорт, СПА, дети, пляж, текст описания.
4. Форматирование в короткий список фактов (5-10 строк) для подстановки
   в промпт ИИ как «РЕАЛЬНЫЕ ОСОБЕННОСТИ ОТЕЛЯ».

Кеширование: см. SheetsClient.get_hotel_description / set_hotel_description.
"""

import logging
import re
import requests
from typing import Optional
from urllib.parse import quote_plus, unquote, parse_qs

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class TopHotelsScraper:
    """
    Ищет и парсит описания отелей на tophotels.ru.

    Пример:
        scraper = TopHotelsScraper()
        text = scraper.get_features("Rixos Premium Belek", "Турция")
        if text:
            print(text)
    """

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})

    # ── Поиск URL отеля через DuckDuckGo ─────────────────────────

    def find_hotel_url(self, name: str, country: str = "") -> Optional[str]:
        """
        Ищет страницу описания отеля на tophotels.ru.
        Возвращает URL вида https://tophotels.ru/hotel/al<ID>/description,
        либо None если не нашли.
        """
        query = f"site:tophotels.ru {name}"
        if country:
            query += f" {country}"
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"TopHotels: DuckDuckGo поиск не удался: {e}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.select("a.result__a"):
            href = link.get("href", "")
            # DDG оборачивает ссылки в /l/?uddg=URL
            if "uddg=" in href:
                qs = parse_qs(href.split("?", 1)[-1])
                href = unquote(qs.get("uddg", [""])[0])
            m = re.search(r"tophotels\.ru/hotel/(al\d+)", href)
            if m:
                hotel_id = m.group(1)
                return f"https://tophotels.ru/hotel/{hotel_id}/description"

        return None

    # ── Парсинг страницы описания ────────────────────────────────

    def parse_description(self, url: str) -> Optional[dict]:
        """Скачивает страницу и возвращает dict с ключевыми фактами."""
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"TopHotels: не удалось получить {url}: {e}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Название + звёзды
        name_el = soup.select_one("span.topline__ttl h1")
        name_raw = name_el.get_text(strip=True) if name_el else ""
        m = re.search(r"(\d)\*", name_raw)
        stars = int(m.group(1)) if m else None
        name = re.sub(r"\s*\d\*\s*$", "", name_raw).strip()

        # Тип и расположение
        hotel_type = [
            a.get_text(strip=True)
            for a in soup.select("div.topline__type a.topline__inline")
        ]
        location = [
            a.get_text(strip=True)
            for a in soup.select("div.topline__location a.topline__inline")
        ]

        # Текст описания
        desc_el = soup.select_one('span[data-item="allocation-description"]')
        description = desc_el.get_text(" ", strip=True) if desc_el else ""

        # Расстояние до моря (из info-таблицы под #place)
        distance = ""
        for tr in soup.select("table.lsfw-fill-tbl tr"):
            td = tr.find("td")
            if td and "Тип расположения" in td.get_text():
                b = tr.select_one("b.lsfw-fill-tbl__inline")
                if b:
                    distance = b.get_text(strip=True)
                break

        # Помощник для секций rest/sport/medic/children
        def section_count_titles(anchor_id: str, max_titles: int = 5):
            h2 = soup.find("h2", id=anchor_id)
            if not h2:
                return 0, []
            ul = h2.find_next("ul", class_="card-hotel-infras")
            if not ul:
                return 0, []
            items = ul.select("li.card-hotel-infra")
            titles = []
            for li in items:
                title = li.select_one("h3.bth__ttl-h3")
                if title:
                    titles.append(title.get_text(strip=True))
            return len(items), titles[:max_titles]

        bars_count, bars_titles   = section_count_titles("bars")
        sport_count, sport_titles = section_count_titles("sport")
        medic_count, medic_titles = section_count_titles("medic")
        kids_count, kids_titles   = section_count_titles("children")

        # Пляж — только пункты "есть" (галочки)
        beach_features = []
        beach_h2 = soup.find("h2", id="beach")
        if beach_h2:
            beach_svc = beach_h2.find_next("div", class_="card-hotel-service")
            if beach_svc:
                for li in beach_svc.select("li.card-hotel-char__check"):
                    beach_features.append(li.get_text(strip=True))
                beach_features = beach_features[:8]

        return {
            "name":              name,
            "stars":             stars,
            "hotel_type":        hotel_type,
            "location":          location,
            "description":       description,
            "distance_to_sea":   distance,
            "bars_count":        bars_count,
            "bars_titles":       bars_titles,
            "sport_count":       sport_count,
            "sport_titles":      sport_titles,
            "medic_count":       medic_count,
            "medic_titles":      medic_titles,
            "kids_count":        kids_count,
            "kids_titles":       kids_titles,
            "beach_features":    beach_features,
            "source_url":        url,
        }

    # ── Форматирование для промпта ──────────────────────────────

    @staticmethod
    def format_for_prompt(data: dict) -> str:
        """
        Превращает dict-описание в короткий текст для подстановки
        в промпт ИИ. Цель: 5-10 строк фактов, без HTML, без воды.
        """
        lines: list[str] = []

        if data.get("hotel_type"):
            lines.append("Тип: " + ", ".join(data["hotel_type"]))

        if data.get("distance_to_sea"):
            lines.append("Море: " + data["distance_to_sea"])

        def _pack(label: str, count: int, titles: list[str]) -> Optional[str]:
            if not count:
                return None
            head = ", ".join(titles[:3])
            tail = f" ({head})" if head else ""
            return f"{label}: {count}{tail}"

        for line in (
            _pack("Рестораны/бары", data.get("bars_count", 0),  data.get("bars_titles", [])),
            _pack("Спорт",          data.get("sport_count", 0), data.get("sport_titles", [])),
            _pack("СПА/wellness",   data.get("medic_count", 0), data.get("medic_titles", [])),
            _pack("Для детей",      data.get("kids_count", 0),  data.get("kids_titles", [])),
        ):
            if line:
                lines.append(line)

        if data.get("beach_features"):
            lines.append("Пляж: " + ", ".join(data["beach_features"][:5]))

        desc = data.get("description") or ""
        if len(desc) > 50:
            short = desc[:300].rstrip()
            if len(desc) > 300:
                short += "..."
            lines.append("Описание: " + short)

        return "\n".join(lines)

    # ── Главная точка входа ─────────────────────────────────────

    def get_features(self, name: str, country: str = "") -> Optional[str]:
        """
        Найти отель → спарсить → отформатировать.
        Возвращает текст для промпта или None если ничего не нашли.
        """
        if not name or not name.strip():
            return None

        url = self.find_hotel_url(name, country)
        if not url:
            logger.info(f"TopHotels: отель не найден — {name!r}")
            return None

        logger.info(f"TopHotels: найден {name!r} → {url}")
        data = self.parse_description(url)
        if not data:
            return None

        return self.format_for_prompt(data)
