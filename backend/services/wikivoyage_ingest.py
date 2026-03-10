from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

import httpx

from settings import Settings

logger = logging.getLogger(__name__)


class WikivoyageIngestService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.data_path.mkdir(parents=True, exist_ok=True)

    async def fetch_city_page(self, city: str) -> tuple[str | None, str]:
        file_path = self.settings.data_path / f"wikivoyage_{city.replace(' ', '_')}.txt"
        if file_path.exists():
            return self._page_url(city), file_path.read_text(encoding="utf-8")

        if self.settings.mock_mode:
            text = self._mock_text(city)
            file_path.write_text(text, encoding="utf-8")
            return self._page_url(city), text

        try:
            base_url = f"https://{self.settings.wikivoyage_lang}.wikivoyage.org/w/api.php"
            params = {
                "action": "query",
                "prop": "extracts",
                "explaintext": 1,
                "titles": city,
                "format": "json",
                "redirects": 1,
            }
            headers = {"User-Agent": "ai-travel-agent-hse/1.0"}
            async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds, headers=headers) as client:
                resp = await client.get(base_url, params=params)
                resp.raise_for_status()
                data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            extract = ""
            for page in pages.values():
                extract = page.get("extract", "")
                if extract:
                    break
            if extract:
                file_path.write_text(extract, encoding="utf-8")
                return self._page_url(city), extract
        except Exception as exc:
            logger.warning("Wikivoyage fetch failed for city=%s, using local fallback: %s", city, exc)

        fallback = self._find_local_fallback(city)
        if fallback is not None:
            return self._page_url(city), fallback.read_text(encoding="utf-8")

        text = self._mock_text(city)
        file_path.write_text(text, encoding="utf-8")
        return self._page_url(city), text

    def _find_local_fallback(self, city: str) -> Path | None:
        exact = self.settings.data_path / f"wikivoyage_{city.replace(' ', '_')}.txt"
        if exact.exists():
            return exact

        normalized = city.lower().replace("ё", "е").replace("-", " ").replace("_", " ")
        for candidate in self.settings.data_path.glob("wikivoyage_*.txt"):
            name = candidate.stem.replace("wikivoyage_", "").lower().replace("ё", "е").replace("-", " ").replace("_", " ")
            if normalized in name or name in normalized:
                return candidate
        return None

    def _page_url(self, city: str) -> str:
        return f"https://{self.settings.wikivoyage_lang}.wikivoyage.org/wiki/{quote(city.replace(' ', '_'))}"

    def _mock_text(self, city: str) -> str:
        return (
            f"{city} is a major travel destination with museums, galleries, historic center walks, cafes, bars, and parks. "
            f"Travelers often combine a morning museum, lunch in the center, an afternoon walk, and evening nightlife. "
            f"For short city breaks, compact routes around the central districts work best. "
        )
