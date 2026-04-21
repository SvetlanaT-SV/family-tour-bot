"""
tourvisor/client.py — Клиент для работы с Tourvisor API

Tourvisor — агрегатор туров для агентств. Через этот файл
мы получаем список горящих туров.

Как работает API (два шага):
  1. Отправляем запрос на поиск → получаем requestid
  2. Опрашиваем результаты по requestid → получаем туры

Документация: https://wiki.tourvisor.ru/
"""

import time
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────────────────────────
# Структура данных одного тура
# ──────────────────────────────────────────────────────────────

@dataclass
class Tour:
    """Один горящий тур со всеми нужными полями"""
    # Откуда летим
    city_from: str = ""
    city_from_id: int = 0

    # Куда летим
    country: str = ""
    country_id: int = 0
    resort: str = ""

    # Отель
    hotel_name: str = ""
    hotel_stars: int = 0
    hotel_id: int = 0

    # Параметры тура
    meal: str = ""           # тип питания: AI, BB, HB, FB, UAI
    nights: int = 0
    date_from: str = ""      # дата вылета, строка "DD.MM.YYYY"
    adults: int = 2
    children: int = 0

    # Цена
    price: float = 0.0       # текущая цена за тур (на двоих)
    price_per_person: float = 0.0  # цена на человека
    currency: str = "RUB"

    # Медиа
    photo_url: str = ""

    # Дополнительно
    operator: str = ""
    tour_id: str = ""
    room_type: str = ""
    distance_sea: int = 0    # в метрах

    @property
    def formatted_price(self) -> str:
        """Цена красиво: 45 000 ₽"""
        return f"{int(self.price):,} ₽".replace(",", " ")

    @property
    def formatted_price_per_person(self) -> str:
        return f"{int(self.price_per_person):,} ₽".replace(",", " ")

    @property
    def stars_str(self) -> str:
        """Звёзды как символы: ⭐⭐⭐⭐⭐"""
        return "⭐" * self.hotel_stars if self.hotel_stars else ""

    @property
    def meal_label(self) -> str:
        """Читаемое название питания"""
        labels = {
            "AI":  "Всё включено",
            "UAI": "Ультра всё включено",
            "BB":  "Завтрак",
            "HB":  "Полупансион",
            "FB":  "Полный пансион",
            "RO":  "Без питания",
        }
        return labels.get(self.meal.upper(), self.meal)

    def __repr__(self):
        return (
            f"🌍 {self.country} | {self.resort}\n"
            f"🏨 {self.hotel_name} {self.stars_str}\n"
            f"✈️  {self.city_from} → {self.date_from}, {self.nights} ночей\n"
            f"🍽  {self.meal_label}\n"
            f"💰 {self.formatted_price_per_person}/чел\n"
            f"🏢 Оператор: {self.operator}"
        )


# ──────────────────────────────────────────────────────────────
# Клиент Tourvisor API
# ──────────────────────────────────────────────────────────────

class TourvisorClient:
    """
    Клиент для работы с Tourvisor API.

    Пример использования:
        client = TourvisorClient("login@email.ru", "password")
        cities = client.get_departure_cities()
        tours = client.find_hot_tours(departure_id=1)
    """

    BASE_URL = "https://tourvisor.ru/xml"

    def __init__(self, login: str, password: str):
        self.login = login
        self.password = password
        # Эти параметры добавляются к каждому запросу
        self._auth = {
            "authlogin": login,
            "authpass":  password,
            "format":    "json",   # хотим JSON, не XML
        }

    def _get(self, endpoint: str, params: dict) -> dict:
        """
        Вспомогательный метод: делает GET-запрос к API.
        Добавляет авторизацию и обрабатывает ошибки.
        """
        url = f"{self.BASE_URL}/{endpoint}"
        all_params = {**self._auth, **params}

        try:
            response = requests.get(url, params=all_params, timeout=30)
            response.raise_for_status()  # вызовет ошибку если код не 200
            return response.json()
        except requests.exceptions.Timeout:
            print(f"⏱ Таймаут запроса к {endpoint}")
            return {}
        except requests.exceptions.RequestException as e:
            print(f"❌ Ошибка запроса к {endpoint}: {e}")
            return {}
        except ValueError:
            print(f"❌ Не удалось разобрать JSON от {endpoint}")
            print(f"   Ответ сервера: {response.text[:200]}")
            return {}

    # ── Справочники ───────────────────────────────────────────

    def get_departure_cities(self) -> list[dict]:
        """
        Возвращает список городов вылета с их кодами.
        Нужно чтобы найти код Уфы.
        """
        data = self._get("list.php", {"type": "departure"})
        # API возвращает: {"lists": {"departures": {"departure": [...]}}}
        return data.get("lists", {}).get("departures", {}).get("departure", [])

    def get_countries(self) -> list[dict]:
        """
        Возвращает список стран назначения с кодами.
        """
        data = self._get("list.php", {"type": "country"})
        return data.get("lists", {}).get("countries", {}).get("country", [])

    def find_city_id(self, city_name: str) -> Optional[int]:
        """
        Ищет ID города по его названию.
        Например: find_city_id("Уфа") → 8
        """
        cities = self.get_departure_cities()
        city_name_lower = city_name.lower()
        for city in cities:
            if city_name_lower in city.get("name", "").lower():
                return city.get("id")
        return None

    # ── Поиск туров ───────────────────────────────────────────

    def _start_search(self, departure_id: int, country_id: Optional[int],
                       nights_from: int, nights_to: int,
                       days_from: int, days_to: int,
                       adults: int, price_max: Optional[int]) -> Optional[str]:
        """
        Шаг 1: Запускает поиск туров.
        Возвращает requestid — ID запроса для получения результатов.
        """
        today = datetime.now()

        params = {
            "departure":   departure_id,
            "nightsfrom":  nights_from,
            "nightsto":    nights_to,
            "datefrom":    (today + timedelta(days=1)).strftime("%d.%m.%Y"),
            "dateto":      (today + timedelta(days=days_to)).strftime("%d.%m.%Y"),
            "adults":      adults,
            "child":       0,
        }

        if country_id:
            params["country"] = country_id
        if price_max:
            params["priceto"] = price_max

        print(f"🔍 Запускаю поиск туров из города ID={departure_id}...")
        data = self._get("search.php", params)

        request_id = data.get("data", {}).get("requestid")
        if not request_id:
            # Попробуем другую структуру ответа
            request_id = data.get("requestid")

        if request_id:
            print(f"   Запрос создан, requestid = {request_id}")
        else:
            print(f"   ❌ Не получили requestid. Ответ API: {data}")

        return request_id

    def _get_results(self, request_id: str, page: int = 1) -> dict:
        """
        Шаг 2: Получает результаты поиска по requestid.
        """
        return self._get("result.php", {
            "requestid": request_id,
            "page":      page,
            "onpage":    50,   # туров на страницу
        })

    def _wait_for_results(self, request_id: str,
                           max_wait_sec: int = 90) -> list[dict]:
        """
        Ожидает завершения поиска и возвращает список туров.
        Tourvisor ищет асинхронно — нужно опрашивать каждую секунду.
        """
        print(f"⏳ Жду результаты (макс {max_wait_sec} сек)...")

        for attempt in range(max_wait_sec):
            data = self._get_results(request_id)
            result = data.get("data", {})
            status = result.get("status")

            # Статус 1/"1"/"ok"/"done" — поиск завершён
            if status in (1, "1", "ok", "done") or str(status) == "1":
                tours_raw = result.get("result", {}).get("hotel", [])
                if isinstance(tours_raw, dict):
                    tours_raw = [tours_raw]
                print(f"✅ Поиск завершён, найдено туров: {len(tours_raw)}, статус={status!r}")
                return tours_raw

            found_so_far = result.get("found", 0)
            if attempt % 5 == 0:
                print(f"   Ищу... ({attempt} сек, статус={status!r}, найдено пока: {found_so_far})")

            time.sleep(1)

        print("⚠️  Превышено время ожидания (90 сек)")
        return []

    def _parse_tour(self, raw: dict) -> Tour:
        """
        Преобразует «сырой» словарь от API в объект Tour.
        """
        # Цена: API может вернуть в разных полях
        price = float(raw.get("price", 0) or raw.get("cost", 0) or 0)
        adults = int(raw.get("adults", 2) or 2)
        price_per_person = price / adults if adults > 0 else price

        return Tour(
            city_from      = raw.get("departurename", ""),
            city_from_id   = int(raw.get("departure", 0) or 0),
            country        = raw.get("countryname", ""),
            country_id     = int(raw.get("country", 0) or 0),
            resort         = raw.get("resortname", ""),
            hotel_name     = raw.get("hotelname", ""),
            hotel_stars    = int(raw.get("hotelstars", 0) or 0),
            hotel_id       = int(raw.get("hotel", 0) or 0),
            meal           = raw.get("mealname", raw.get("meal", "")),
            nights         = int(raw.get("nights", 0) or 0),
            date_from      = raw.get("flydate", raw.get("datefrom", "")),
            adults         = adults,
            children       = int(raw.get("child", 0) or 0),
            price          = price,
            price_per_person = price_per_person,
            currency       = raw.get("currency", "RUB"),
            photo_url      = raw.get("photourl", raw.get("photo", "")),
            operator       = raw.get("operatorname", raw.get("operator", "")),
            tour_id        = str(raw.get("tourid", raw.get("id", ""))),
            room_type      = raw.get("roomname", raw.get("room", "")),
            distance_sea   = int(raw.get("sea", 0) or 0),
        )

    def find_hot_tours(
        self,
        departure_id:    int,
        country_id:      Optional[int] = None,
        nights_from:     int = 7,
        nights_to:       int = 14,
        days_ahead:      int = 14,
        adults:          int = 2,
        price_max:       Optional[int] = None,
        min_discount_pct: Optional[float] = None,
    ) -> list[Tour]:
        """
        Главный метод: ищет горящие туры и возвращает список Tour.

        Параметры:
            departure_id    — ID города вылета (Уфа = найти через get_departure_cities)
            country_id      — ID страны (None = все страны)
            nights_from     — минимум ночей
            nights_to       — максимум ночей
            days_ahead      — искать вылеты в ближайшие N дней
            adults          — количество взрослых
            price_max       — максимальная цена за тур (в рублях)
            min_discount_pct — минимальная скидка в % (пока не используется напрямую в API)
        """
        # Шаг 1: запускаем поиск
        request_id = self._start_search(
            departure_id=departure_id,
            country_id=country_id,
            nights_from=nights_from,
            nights_to=nights_to,
            days_from=1,
            days_to=days_ahead,
            adults=adults,
            price_max=price_max,
        )

        if not request_id:
            print("❌ Не удалось запустить поиск")
            return []

        # Шаг 2: ждём результаты
        raw_tours = self._wait_for_results(request_id)

        # Шаг 3: разбираем сырые данные в объекты Tour
        tours = [self._parse_tour(r) for r in raw_tours]

        # Шаг 4: фильтрация (дополнительная, поверх API)
        if price_max:
            tours = [t for t in tours if t.price <= price_max]

        # Сортируем по цене — дешевле вверху
        tours.sort(key=lambda t: t.price)

        return tours
