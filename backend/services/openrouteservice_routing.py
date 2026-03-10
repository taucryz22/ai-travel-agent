from __future__ import annotations

import logging
import math

import httpx

from settings import Settings

logger = logging.getLogger(__name__)

ORS_PROFILE_BY_MODE = {
    "walking": "foot-walking",
    "driving": "driving-car",
}


class OpenRouteServiceRoutingService:
    def __init__(self, settings: Settings, cache):
        self.settings = settings
        self.cache = cache

    async def travel_minutes(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
    ) -> int:
        info = await self.travel_info(origin, destination, mode)
        return int(info["minutes"])

    async def travel_distance_km(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
    ) -> float:
        info = await self.travel_info(origin, destination, mode)
        return float(info["distance_km"])

    async def travel_info(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
    ) -> dict[str, float | int]:
        cache_key = f"ors_route_info:{origin}:{destination}:{mode}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if self.settings.mock_mode:
            result = self._fallback_info(origin, destination, mode)
            self.cache.set(cache_key, result)
            return result

        if mode == "transit":
            walking = await self.travel_info(origin, destination, "walking")
            driving = await self.travel_info(origin, destination, "driving")
            result = {
                "minutes": self._estimate_transit_minutes(
                    int(walking["minutes"]),
                    int(driving["minutes"]),
                ),
                "distance_km": float(driving["distance_km"]),
            }
            self.cache.set(cache_key, result)
            return result

        profile = ORS_PROFILE_BY_MODE.get(mode, "driving-car")

        try:
            result = await self._fetch_matrix_info(origin, destination, profile)
        except Exception as exc:
            logger.warning(
                "ORS routing fallback used for mode=%s origin=%s destination=%s due to: %s",
                mode,
                origin,
                destination,
                exc,
            )
            result = self._fallback_info(origin, destination, mode)

        self.cache.set(cache_key, result)
        return result

    async def travel_options(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        allowed_modes: list[str] | None = None,
    ) -> dict[str, dict[str, float | int]]:
        modes = allowed_modes or ["walking", "transit", "driving"]
        result: dict[str, dict[str, float | int]] = {}

        for mode in modes:
            try:
                result[mode] = await self.travel_info(origin, destination, mode)
            except Exception as exc:
                logger.warning(
                    "travel_options fallback for mode=%s origin=%s destination=%s due to: %s",
                    mode,
                    origin,
                    destination,
                    exc,
                )
                result[mode] = self._fallback_info(origin, destination, mode)

        return result

    async def _fetch_matrix_info(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        profile: str,
    ) -> dict[str, float | int]:
        if not self.settings.openrouteservice_api_key:
            raise RuntimeError("OPENROUTESERVICE_API_KEY is missing")

        url = f"{self.settings.openrouteservice_base_url.rstrip('/')}/v2/matrix/{profile}"
        headers = {
            "Authorization": self.settings.openrouteservice_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = {
            "locations": [[origin[1], origin[0]], [destination[1], destination[0]]],
            "sources": [0],
            "destinations": [1],
            "metrics": ["duration", "distance"],
            "units": "km",
        }

        last_exc: Exception | None = None

        for attempt in range(self.settings.retry_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json=body)

                if response.status_code == 429:
                    raise RuntimeError(f"ORS rate limit hit for profile={profile}")

                response.raise_for_status()
                data = response.json()

                durations = data.get("durations") or []
                distances = data.get("distances") or []

                if not durations or not durations[0] or durations[0][0] is None:
                    raise RuntimeError(f"ORS matrix returned empty durations: {data}")

                seconds = float(durations[0][0])

                distance_km = 0.0
                if distances and distances[0] and distances[0][0] is not None:
                    distance_km = float(distances[0][0])

                return {
                    "minutes": max(1, int(round(seconds / 60))),
                    "distance_km": round(distance_km, 2),
                }

            except Exception as exc:
                last_exc = exc
                if attempt >= self.settings.retry_attempts:
                    break

        raise RuntimeError(f"OpenRouteService routing failed: {last_exc}") from last_exc

    def _estimate_transit_minutes(self, walking: int, driving: int) -> int:
        if driving <= 0 and walking <= 0:
            return 20
        if driving <= 0:
            return max(10, int(walking * 0.55))
        if walking <= 0:
            return max(10, int(driving * 1.6))

        estimated = max(driving * 1.6, walking * 0.45)
        return max(10, int(round(estimated)))

    def _fallback_info(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
    ) -> dict[str, float | int]:
        km = self._haversine_km(origin[0], origin[1], destination[0], destination[1])
        road_factor = 1.25
        adjusted_km = km * road_factor

        if mode == "walking":
            speed_kmh = 4.8
        elif mode == "driving":
            speed_kmh = 28.0
        elif mode == "transit":
            speed_kmh = 18.0
        else:
            speed_kmh = 20.0

        minutes = (adjusted_km / max(speed_kmh, 1e-6)) * 60.0

        if mode == "walking":
            final_minutes = max(3, int(round(minutes)))
        elif mode == "driving":
            final_minutes = max(5, int(round(minutes)))
        elif mode == "transit":
            final_minutes = max(7, int(round(minutes)))
        else:
            final_minutes = max(5, int(round(minutes)))

        return {
            "minutes": final_minutes,
            "distance_km": round(adjusted_km, 2),
        }

    def _haversine_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)

        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return r * c
