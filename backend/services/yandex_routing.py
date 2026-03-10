from __future__ import annotations

import logging
from typing import Any

import httpx

from settings import Settings

logger = logging.getLogger(__name__)


class YandexRoutingService:
    def __init__(self, settings: Settings, cache):
        self.settings = settings
        self.cache = cache
        self.base_url = "https://api.routing.yandex.net/v2/route"

    async def travel_minutes(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
    ) -> int:
        cache_key = f"route:{origin}:{destination}:{mode}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if self.settings.mock_mode:
            minutes = self._mock_minutes(origin, destination, mode)
            self.cache.set(cache_key, minutes)
            return minutes

        minutes = await self._fetch_route_minutes(origin, destination, mode)
        self.cache.set(cache_key, minutes)
        return minutes

    async def travel_options(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        allowed_modes: list[str] | None = None,
    ) -> dict[str, int]:
        modes = allowed_modes or ["walking", "transit", "driving"]
        result: dict[str, int] = {}
        for mode in modes:
            result[mode] = await self.travel_minutes(origin, destination, mode)
        return result

    async def _fetch_route_minutes(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
    ) -> int:
        params = {
            "apikey": self.settings.yandex_routing_api_key,
            "waypoints": f"{origin[0]},{origin[1]}|{destination[0]},{destination[1]}",
            "mode": mode,
            "lang": "ru_RU",
        }

        attempts = 2
        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=20) as client:
            for attempt in range(attempts):
                try:
                    response = await client.get(self.base_url, params=params)
                    if response.status_code == 429 and attempt < attempts - 1:
                        continue
                    response.raise_for_status()
                    data = response.json()
                    return self._extract_minutes(data)
                except Exception as exc:
                    last_error = exc

        logger.exception("Routing request failed")
        raise RuntimeError(f"Routing API failed: {last_error}")

    def _extract_minutes(self, data: dict[str, Any]) -> int:
        routes = data.get("routes") or []
        if not routes:
            return 999

        route = routes[0]

        duration_seconds = (
            route.get("duration")
            or route.get("durationInTraffic")
            or route.get("summary", {}).get("duration")
            or route.get("properties", {}).get("Time")
        )

        if isinstance(duration_seconds, (int, float)):
            return max(1, round(duration_seconds / 60))

        legs = route.get("legs") or []
        total_seconds = 0
        for leg in legs:
            leg_duration = leg.get("duration")
            if isinstance(leg_duration, (int, float)):
                total_seconds += leg_duration

        if total_seconds > 0:
            return max(1, round(total_seconds / 60))

        return 999

    def _mock_minutes(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
    ) -> int:
        # very rough demo estimation
        lat1, lon1 = origin
        lat2, lon2 = destination
        dist = ((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) ** 0.5

        base = max(5, int(dist * 800))
        if mode == "walking":
            return max(6, base)
        if mode == "transit":
            return max(8, int(base * 0.55))
        if mode == "driving":
            return max(7, int(base * 0.45))
        return max(8, int(base * 0.6))