from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from models import PlaceCandidate
from settings import Settings
from utils.cache import TTLCache

logger = logging.getLogger(__name__)


class OSMPlacesService:
    def __init__(self, settings: Settings, cache: TTLCache):
        self.settings = settings
        self.cache = cache

    async def search(self, text: str, results: int = 10) -> list[PlaceCandidate]:
        cache_key = f"osm_places::{text}::{results}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if self.settings.mock_mode:
            data = self._mock_search(text, results)
            self.cache.set(cache_key, data)
            return data

        items = await self._search_nominatim(text, results)
        self.cache.set(cache_key, items)
        return items

    async def search_many(
        self,
        texts: list[str],
        city: str | None = None,
        results_per_query: int = 5,
    ) -> list[PlaceCandidate]:
        collected: list[PlaceCandidate] = []
        seen: set[str] = set()

        city = (city or "").strip()
        normalized_city = city.lower()

        unique_queries: list[str] = []
        for text in texts:
            q = text.strip()
            if not q:
                continue

            full = f"{q}, {city}" if city and normalized_city not in q.lower() else q
            if full not in unique_queries:
                unique_queries.append(full)

            if len(unique_queries) >= 6:
                break

        # Если запросов мало, добавим city-only fallback заранее
        if city:
            for extra in self._city_fallback_queries(city):
                if extra not in unique_queries:
                    unique_queries.append(extra)
                if len(unique_queries) >= 10:
                    break

        for idx, query in enumerate(unique_queries):
            if idx > 0 and not self.settings.mock_mode:
                await asyncio.sleep(1.05)

            items = await self.search(query, results=results_per_query)

            # Если по узкому запросу пусто — пробуем расширить
            if not items and city:
                expanded_queries = self._expanded_queries(query, city)
                for extra_query in expanded_queries:
                    await asyncio.sleep(1.05)
                    extra_items = await self.search(extra_query, results=results_per_query)
                    if extra_items:
                        items = extra_items
                        break

            filtered_items = self._filter_for_city(items, city)

            for item in filtered_items:
                key = f"{item.name.lower()}::{item.address.lower()}::{round(item.lat, 4)}::{round(item.lon, 4)}"
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)

        # Финальный мягкий fallback: если вообще пусто, ищем просто по городу
        if not collected and city:
            logger.warning("OSM search_many returned 0 places for city=%s, using final broad fallback", city)
            for broad_query in self._city_fallback_queries(city):
                await asyncio.sleep(1.05)
                items = await self.search(broad_query, results=results_per_query)
                filtered_items = self._filter_for_city(items, city)
                for item in filtered_items:
                    key = f"{item.name.lower()}::{item.address.lower()}::{round(item.lat, 4)}::{round(item.lon, 4)}"
                    if key in seen:
                        continue
                    seen.add(key)
                    collected.append(item)

        return collected

    async def _search_nominatim(self, text: str, results: int) -> list[PlaceCandidate]:
        params = {
            "q": text,
            "format": "jsonv2",
            "limit": min(max(results, 1), 10),
            "addressdetails": 1,
            "extratags": 1,
            "namedetails": 1,
        }

        if self.settings.nominatim_email:
            params["email"] = self.settings.nominatim_email

        headers = {
            "User-Agent": self.settings.nominatim_user_agent,
            "Accept-Language": "ru,en;q=0.8",
            "Referer": "http://localhost:5173",
        }

        url = f"{self.settings.nominatim_base_url.rstrip('/')}/search"
        async with httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds,
            headers=headers,
        ) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        return self._parse_search_results(data, source_query=text)

    def _parse_search_results(self, payload: list[dict[str, Any]], source_query: str) -> list[PlaceCandidate]:
        out: list[PlaceCandidate] = []

        for item in payload:
            lat = item.get("lat")
            lon = item.get("lon")
            display_name = item.get("display_name", "")
            if lat is None or lon is None:
                continue

            name = self._extract_name(item, display_name)
            address = self._extract_address(item, display_name)
            category = self._normalize_category(item, source_query, display_name)
            categories_raw = [v for v in [item.get("class"), item.get("type")] if v]
            confidence = self._category_confidence(category, source_query, display_name)

            out.append(
                PlaceCandidate(
                    name=name,
                    address=address,
                    lat=float(lat),
                    lon=float(lon),
                    category=category,
                    categories_raw=categories_raw,
                    hours_text=None,
                    hours_intervals=[],
                    source_query=source_query,
                    rating=None,
                    reviews_count=None,
                    category_confidence=confidence,
                )
            )

        return out

    def _extract_name(self, item: dict[str, Any], fallback: str) -> str:
        namedetails = item.get("namedetails") or {}
        for key in ["name", "name:ru", "official_name", "brand"]:
            value = namedetails.get(key) or item.get(key)
            if value:
                return str(value)
        return fallback.split(",")[0].strip() if fallback else "Unknown place"

    def _extract_address(self, item: dict[str, Any], fallback: str) -> str:
        address = item.get("address") or {}
        preferred = [
            address.get("road"),
            address.get("house_number"),
            address.get("city") or address.get("town") or address.get("village") or address.get("municipality"),
        ]
        compact = ", ".join([p for p in preferred if p])
        return compact or fallback

    def _normalize_category(self, item: dict[str, Any], source_query: str, display_name: str) -> str:
        haystack = " ".join(
            str(x).lower()
            for x in [
                item.get("class", ""),
                item.get("type", ""),
                item.get("category", ""),
                item.get("display_name", ""),
                source_query,
                display_name,
            ]
        )

        if any(k in haystack for k in ["museum", "музей"]):
            return "museum"
        if any(k in haystack for k in ["gallery", "галере", "exhibition", "выстав", "искусств"]):
            return "gallery"
        if any(k in haystack for k in ["bar", "pub", "bier", "бар", "паб"]):
            return "bar"
        if any(k in haystack for k in ["cafe", "restaurant", "coffee", "кафе", "ресторан", "кофе"]):
            return "cafe"
        if any(k in haystack for k in ["park", "garden", "парк", "сад", "сквер", "набереж"]):
            return "park"
        if any(k in haystack for k in ["attraction", "memorial", "monument", "cathedral", "sight", "достопримеч", "собор", "кремл"]):
            return "landmark"

        # Мягкая классификация по запросу
        sq = source_query.lower()
        if "достопримеч" in sq:
            return "landmark"
        if "музей" in sq:
            return "museum"
        if "галере" in sq or "искусств" in sq:
            return "gallery"
        if "ресторан" in sq or "кафе" in sq or "кухн" in sq:
            return "cafe"
        if "бар" in sq:
            return "bar"
        if "прогул" in sq or "парк" in sq:
            return "park"

        return "other"

    def _category_confidence(self, category: str, query: str, display_name: str) -> float:
        if category == "other":
            return 0.45

        hay = f"{query} {display_name}".lower()
        boosts = {
            "museum": ["museum", "музей"],
            "gallery": ["gallery", "галере", "искусств", "выстав"],
            "bar": ["bar", "бар", "паб", "рок"],
            "cafe": ["cafe", "кафе", "restaurant", "еда", "кофе", "кухн"],
            "park": ["park", "парк", "прогул", "набереж"],
            "landmark": ["достопримеч", "центр", "landmark", "кремл", "собор", "памятник"],
        }
        tokens = boosts.get(category, [])
        matched = any(token in hay for token in tokens)
        return 0.85 if matched else 0.65

    def _filter_for_city(self, items: list[PlaceCandidate], city: str | None) -> list[PlaceCandidate]:
        if not city:
            return items

        city_norm = city.lower().replace("ё", "е")
        strict: list[PlaceCandidate] = []
        relaxed: list[PlaceCandidate] = []

        for item in items:
            hay = f"{item.name} {item.address}".lower().replace("ё", "е")
            if city_norm in hay:
                strict.append(item)
            else:
                relaxed.append(item)

        # Если есть точные совпадения по городу — отлично
        if strict:
            return strict

        # Если точных нет, не валим всё в ноль.
        # Для Nominatim иногда город не попадает в компактный адрес.
        return items

    def _expanded_queries(self, query: str, city: str) -> list[str]:
        q = query.lower()
        expanded: list[str] = []

        if "достопримеч" in q:
            expanded.extend([
                f"достопримечательность, {city}",
                f"памятник, {city}",
                f"собор, {city}",
                f"интересные места, {city}",
            ])
        elif "ресторан" in q or "кухн" in q:
            expanded.extend([
                f"ресторан, {city}",
                f"кафе, {city}",
                f"еда, {city}",
            ])
        elif "кафе" in q or "кофе" in q:
            expanded.extend([
                f"кафе, {city}",
                f"кофейня, {city}",
                f"ресторан, {city}",
            ])
        elif "музей" in q:
            expanded.extend([
                f"музей, {city}",
                f"история, {city}",
            ])
        elif "галере" in q or "искусств" in q:
            expanded.extend([
                f"галерея, {city}",
                f"музей, {city}",
                f"искусство, {city}",
            ])
        elif "бар" in q:
            expanded.extend([
                f"бар, {city}",
                f"паб, {city}",
                f"кафе, {city}",
            ])
        elif "парк" in q or "прогул" in q:
            expanded.extend([
                f"парк, {city}",
                f"сквер, {city}",
                f"набережная, {city}",
            ])

        if not expanded:
            expanded.extend(self._city_fallback_queries(city))

        # убираем дубли
        result: list[str] = []
        for x in expanded:
            if x not in result:
                result.append(x)
        return result[:6]

    def _city_fallback_queries(self, city: str) -> list[str]:
        return [
            f"достопримечательности, {city}",
            f"музей, {city}",
            f"ресторан, {city}",
            f"кафе, {city}",
            f"центр города, {city}",
        ]

    def _mock_search(self, text: str, results: int) -> list[PlaceCandidate]:
        text_lower = text.lower()
        if "питер" in text_lower or "санкт" in text_lower:
            city = "Санкт-Петербург"
        elif "казан" in text_lower:
            city = "Казань"
        elif "чебоксар" in text_lower:
            city = "Чебоксары"
        else:
            city = "Москва"

        catalog = MOCK_PLACES.get(city, MOCK_PLACES["Москва"])
        matched = [item for item in catalog if any(tok in item["tags"] for tok in _tokens(text_lower))]
        if not matched:
            matched = catalog

        return [
            PlaceCandidate(**{k: v for k, v in item.items() if k != "tags"}, source_query=text)
            for item in matched[:results]
        ]


def _tokens(text: str) -> list[str]:
    return [part.strip().lower() for part in re.split(r"[,\s]+", text) if len(part.strip()) > 2]


MOCK_PLACES = {
    "Санкт-Петербург": [
        {"name": "Эрарта", "address": "29-я линия В.О., 2", "lat": 59.9219, "lon": 30.2487, "category": "gallery", "categories_raw": ["Музей", "Галерея"], "hours_text": "11:00–23:00", "hours_intervals": [], "tags": ["искусство", "современное", "галерея", "музей"]},
        {"name": "Русский музей", "address": "Инженерная ул., 4", "lat": 59.9386, "lon": 30.3347, "category": "museum", "categories_raw": ["Музей"], "hours_text": "10:00–18:00", "hours_intervals": [], "tags": ["музей", "искусство", "классика"]},
        {"name": "Новая Голландия", "address": "наб. Адмиралтейского канала, 2", "lat": 59.9287, "lon": 30.2906, "category": "park", "categories_raw": ["Парк"], "hours_text": "10:00–22:00", "hours_intervals": [], "tags": ["прогулка", "парк", "остров"]},
        {"name": "The Hat Bar", "address": "ул. Белинского, 9", "lat": 59.9412, "lon": 30.3497, "category": "bar", "categories_raw": ["Бар"], "hours_text": "18:00–03:00", "hours_intervals": [], "tags": ["рок", "бар", "джаз", "вечер"]},
        {"name": "Civil Coffee", "address": "Гражданская ул., 13-15", "lat": 59.9276, "lon": 30.3174, "category": "cafe", "categories_raw": ["Кафе"], "hours_text": "09:00–22:00", "hours_intervals": [], "tags": ["кофе", "обед", "кафе"]},
        {"name": "Исаакиевский собор", "address": "Исаакиевская пл., 4", "lat": 59.9343, "lon": 30.3061, "category": "landmark", "categories_raw": ["Достопримечательность"], "hours_text": "10:00–18:00", "hours_intervals": [], "tags": ["достопримечательность", "центр", "история"]},
    ],
    "Казань": [
        {"name": "Казанский Кремль", "address": "Кремлёвская ул., 2", "lat": 55.7989, "lon": 49.1068, "category": "landmark", "categories_raw": ["Достопримечательность"], "hours_text": "08:00–22:00", "hours_intervals": [], "tags": ["история", "центр", "кремль"]},
        {"name": "Центр Эрмитаж-Казань", "address": "Проезд Шейнкмана, 12", "lat": 55.7986, "lon": 49.1062, "category": "gallery", "categories_raw": ["Галерея"], "hours_text": "10:00–18:00", "hours_intervals": [], "tags": ["искусство", "музей", "галерея"]},
        {"name": "Дом татарской кулинарии", "address": "ул. Баумана, 31", "lat": 55.7893, "lon": 49.1221, "category": "cafe", "categories_raw": ["Ресторан"], "hours_text": "10:00–23:00", "hours_intervals": [], "tags": ["еда", "кухня", "обед", "ресторан"]},
        {"name": "Черное озеро", "address": "ул. Дзержинского", "lat": 55.7944, "lon": 49.1189, "category": "park", "categories_raw": ["Парк"], "hours_text": "Круглосуточно", "hours_intervals": [], "tags": ["прогулка", "парк", "спокойно"]},
    ],
    "Чебоксары": [
        {"name": "Чебоксарский залив", "address": "набережная Чебоксарского залива", "lat": 56.1431, "lon": 47.2489, "category": "landmark", "categories_raw": ["Набережная"], "hours_text": "Круглосуточно", "hours_intervals": [], "tags": ["достопримечательность", "центр", "прогулка"]},
        {"name": "Музей истории трактора", "address": "просп. Мира, 1", "lat": 56.1329, "lon": 47.2749, "category": "museum", "categories_raw": ["Музей"], "hours_text": "10:00–18:00", "hours_intervals": [], "tags": ["музей", "история"]},
        {"name": "Парк 500-летия Чебоксар", "address": "Московский проспект", "lat": 56.1463, "lon": 47.2037, "category": "park", "categories_raw": ["Парк"], "hours_text": "Круглосуточно", "hours_intervals": [], "tags": ["парк", "прогулка"]},
        {"name": "Кафе на заливе", "address": "центр, Чебоксары", "lat": 56.1420, "lon": 47.2510, "category": "cafe", "categories_raw": ["Кафе"], "hours_text": "10:00–22:00", "hours_intervals": [], "tags": ["еда", "кафе", "обед"]},
    ],
    "Москва": [
        {"name": "ГЭС-2", "address": "Болотная наб., 15", "lat": 55.7445, "lon": 37.6090, "category": "gallery", "categories_raw": ["Галерея"], "hours_text": "11:00–22:00", "hours_intervals": [], "tags": ["искусство", "галерея", "современное"]},
        {"name": "Парк Горького", "address": "Крымский Вал, 9", "lat": 55.7299, "lon": 37.6034, "category": "park", "categories_raw": ["Парк"], "hours_text": "Круглосуточно", "hours_intervals": [], "tags": ["парк", "прогулка"]},
        {"name": "Mendeleev Bar", "address": "Петровка, 20/1", "lat": 55.7625, "lon": 37.6171, "category": "bar", "categories_raw": ["Бар"], "hours_text": "18:00–03:00", "hours_intervals": [], "tags": ["бар", "вечер"]},
    ],
}
