from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

import httpx

from models import PlaceCandidate
from settings import Settings
from utils.cache import TTLCache

logger = logging.getLogger(__name__)
SEARCH_URL = "https://search-maps.yandex.ru/v1/"


class YandexPlacesService:
    def __init__(self, settings: Settings, cache: TTLCache):
        self.settings = settings
        self.cache = cache

    async def search(self, text: str, results: int = 10) -> list[PlaceCandidate]:
        cache_key = f"places::{text}::{results}::{self.settings.yandex_lang}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if self.settings.mock_mode:
            data = self._mock_search(text, results)
            self.cache.set(cache_key, data)
            return data

        if not self.settings.yandex_places_api_key:
            raise ValueError("YANDEX_PLACES_API_KEY is missing")

        params = {
            "apikey": self.settings.yandex_places_api_key,
            "text": text,
            "type": "biz",
            "lang": self.settings.yandex_lang,
            "results": results,
        }
        data = await self._request_with_retry(SEARCH_URL, params)
        items = self._parse_features(data, source_query=text)
        self.cache.set(cache_key, items)
        return items


    async def search_many(self, texts: list[str], city: str | None = None, results_per_query: int = 5) -> list[PlaceCandidate]:
        collected: list[PlaceCandidate] = []
        seen: set[str] = set()

        for text in texts:
            query = f"{text} {city}".strip() if city and city.lower() not in text.lower() else text
            for item in await self.search(query, results=results_per_query):
                key = f"{item.name.lower()}::{item.address.lower()}::{round(item.lat, 4)}::{round(item.lon, 4)}"
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)

        return collected

    async def _request_with_retry(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.settings.retry_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds) as client:
                    resp = await client.get(url, params=params)
                if resp.status_code == 429 and attempt < self.settings.retry_attempts:
                    logger.warning("Places API 429, retrying attempt=%s", attempt + 1)
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_exc = exc
                if attempt >= self.settings.retry_attempts:
                    raise
        raise RuntimeError(str(last_exc))

    def _parse_features(self, payload: dict[str, Any], source_query: str) -> list[PlaceCandidate]:
        features = payload.get("features", [])
        out: list[PlaceCandidate] = []
        for feature in features:
            props = feature.get("properties", {})
            meta = props.get("CompanyMetaData", {})
            geometry = feature.get("geometry", {})
            coords = geometry.get("coordinates") or [None, None]
            if len(coords) != 2:
                continue
            lon, lat = coords  # Yandex Search returns [lon, lat]
            categories = [c.get("name", "") for c in meta.get("Categories", []) if c.get("name")]
            hours = meta.get("Hours", {}) or {}
            category = self._normalize_category((meta.get("Categories") or []), props.get("name", ""))
            out.append(
                PlaceCandidate(
                    name=props.get("name", "Unknown place"),
                    address=meta.get("address", props.get("description", "")) or props.get("description", ""),
                    lat=float(lat),
                    lon=float(lon),
                    category=category,
                    categories_raw=categories,
                    hours_text=hours.get("text"),
                    hours_intervals=hours.get("Availabilities", []) or hours.get("availabilities", []) or [],
                    source_query=source_query,
                )
            )
        return out

    def _normalize_category(self, categories_meta: list[dict[str, Any]], name: str) -> str:
        names = " ".join([c.get("name", "") for c in categories_meta]).lower() + f" {name.lower()}"
        if any(k in names for k in ["музей", "museum"]):
            return "museum"
        if any(k in names for k in ["галере", "gallery", "выстав"]):
            return "gallery"
        if any(k in names for k in ["bar", "бар", "pub", "паб"]):
            return "bar"
        if any(k in names for k in ["cafe", "кафе", "restaurant", "ресторан", "кофе"]):
            return "cafe"
        if any(k in names for k in ["park", "парк", "сад", "сквер"]):
            return "park"
        if any(k in names for k in ["достопримеч", "landmark", "cathedral", "собор", "крепост"]):
            return "landmark"
        return "other"

    def _mock_search(self, text: str, results: int) -> list[PlaceCandidate]:
        text_lower = text.lower()
        city = "Санкт-Петербург" if "питер" in text_lower or "санкт" in text_lower else "Казань" if "казан" in text_lower else "Москва"
        catalog = MOCK_PLACES.get(city, MOCK_PLACES["Москва"])
        matched = [item for item in catalog if any(tok in item["tags"] for tok in _tokens(text_lower))]
        if not matched:
            matched = catalog
        items = [PlaceCandidate(**{k: v for k, v in item.items() if k != "tags"}, source_query=text) for item in matched[:results]]
        return items


def _tokens(text: str) -> list[str]:
    return [part.strip().lower() for part in text.replace(",", " ").split() if len(part.strip()) > 2]


MOCK_PLACES = {
    "Санкт-Петербург": [
        {"name": "Эрарта", "address": "29-я линия В.О., 2", "lat": 59.9219, "lon": 30.2487, "category": "gallery", "categories_raw": ["Музей", "Галерея"], "hours_text": "11:00–23:00", "hours_intervals": [], "tags": ["искусство", "современное", "галерея", "музей"]},
        {"name": "Русский музей", "address": "Инженерная ул., 4", "lat": 59.9386, "lon": 30.3347, "category": "museum", "categories_raw": ["Музей"], "hours_text": "10:00–18:00", "hours_intervals": [], "tags": ["музей", "искусство", "классика"]},
        {"name": "Новая Голландия", "address": "наб. Адмиралтейского канала, 2", "lat": 59.9287, "lon": 30.2906, "category": "park", "categories_raw": ["Парк"], "hours_text": "10:00–22:00", "hours_intervals": [], "tags": ["прогулка", "парк", "остров"]},
        {"name": "The Hat Bar", "address": "ул. Белинского, 9", "lat": 59.9412, "lon": 30.3497, "category": "bar", "categories_raw": ["Бар"], "hours_text": "18:00–03:00", "hours_intervals": [], "tags": ["рок", "бар", "джаз", "вечер"]},
        {"name": "Redrum", "address": "Лиговский просп., 50", "lat": 59.9258, "lon": 30.3603, "category": "bar", "categories_raw": ["Бар"], "hours_text": "18:00–02:00", "hours_intervals": [], "tags": ["рок", "бар", "концерт"]},
        {"name": "Civil Coffee", "address": "Гражданская ул., 13-15", "lat": 59.9276, "lon": 30.3174, "category": "cafe", "categories_raw": ["Кафе"], "hours_text": "09:00–22:00", "hours_intervals": [], "tags": ["кофе", "обед", "кафе"]},
        {"name": "Исаакиевский собор", "address": "Исаакиевская пл., 4", "lat": 59.9343, "lon": 30.3061, "category": "landmark", "categories_raw": ["Достопримечательность"], "hours_text": "10:00–18:00", "hours_intervals": [], "tags": ["достопримечательность", "центр", "история"]},
    ],
    "Казань": [
        {"name": "Казанский Кремль", "address": "Кремлёвская ул., 2", "lat": 55.7989, "lon": 49.1068, "category": "landmark", "categories_raw": ["Достопримечательность"], "hours_text": "08:00–22:00", "hours_intervals": [], "tags": ["история", "центр", "кремль"]},
        {"name": "Центр ""Эрмитаж-Казань""", "address": "Проезд Шейнкмана, 12", "lat": 55.7986, "lon": 49.1062, "category": "gallery", "categories_raw": ["Галерея"], "hours_text": "10:00–18:00", "hours_intervals": [], "tags": ["искусство", "музей", "галерея"]},
        {"name": "Дом татарской кулинарии", "address": "ул. Баумана, 31", "lat": 55.7893, "lon": 49.1221, "category": "cafe", "categories_raw": ["Ресторан"], "hours_text": "10:00–23:00", "hours_intervals": [], "tags": ["еда", "кухня", "обед", "ресторан"]},
        {"name": "Черное озеро", "address": "ул. Дзержинского", "lat": 55.7944, "lon": 49.1189, "category": "park", "categories_raw": ["Парк"], "hours_text": "Круглосуточно", "hours_intervals": [], "tags": ["прогулка", "парк", "спокойно"]},
    ],
    "Москва": [
        {"name": "ГЭС-2", "address": "Болотная наб., 15", "lat": 55.7445, "lon": 37.6090, "category": "gallery", "categories_raw": ["Галерея"], "hours_text": "11:00–22:00", "hours_intervals": [], "tags": ["искусство", "галерея", "современное"]},
        {"name": "Парк Горького", "address": "Крымский Вал, 9", "lat": 55.7299, "lon": 37.6034, "category": "park", "categories_raw": ["Парк"], "hours_text": "Круглосуточно", "hours_intervals": [], "tags": ["парк", "прогулка"]},
        {"name": "Mendeleev Bar", "address": "Петровка, 20/1", "lat": 55.7625, "lon": 37.6171, "category": "bar", "categories_raw": ["Бар"], "hours_text": "18:00–03:00", "hours_intervals": [], "tags": ["бар", "вечер"]},
    ],
}
