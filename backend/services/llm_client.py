from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from models import Intent
from settings import Settings

logger = logging.getLogger(__name__)

TAIL_AFTER_CITY = r"(?:\s+на\s+\d+\s+(?:день|дня|дней|ночь|ночи|ночей))?"


EXTRACT_PROMPT = """Ты извлекаешь интент туристического запроса пользователя.
Нужно вернуть только JSON без пояснений и без markdown.

Верни JSON строго по схеме:
{
  "origin_city": string | null,
  "destination_city": string | null,
  "route_city": string | null,
  "trip_scope": "in_city" | "between_cities" | "unknown",
  "interests": string[],
  "vibe": string,
  "pace": string
}

Правила:
1. Если пользователь едет ИЗ одного города В другой:
   - origin_city = город отправления
   - destination_city = город назначения
   - route_city = город назначения
   - trip_scope = "between_cities"

2. Если пользователь уже находится в городе и хочет маршрут внутри него:
   - origin_city = null
   - destination_city = этот город
   - route_city = этот город
   - trip_scope = "in_city"

3. Если упомянут только один город, и маршрут нужен по нему:
   - destination_city = этот город
   - route_city = этот город

4. Названия городов обязательно верни:
   - на русском языке
   - в нормальной канонической форме
   - в именительном падеже
   - без предлогов

5. interests — это интересы пользователя, кратко и по смыслу, на русском.
6. vibe — одно из: nightlife, culture, food, nature, urban, balanced
7. pace — одно из: slow, moderate, intense
8. Не используй английские названия городов.
9. Не добавляй никакого текста, кроме JSON.
"""

PHRASES_PROMPT = """Ты генерируешь поисковые фразы для поиска мест в городе.
Верни только JSON без пояснений и без markdown.

Схема:
{
  "phrases": string[]
}

Правила:
1. Фразы должны быть на русском.
2. Используй нормальное русское название города.
3. Дай 6-10 полезных поисковых фраз.
4. Фразы должны отражать интересы пользователя и город.
5. Не пиши адреса.
6. Не пиши ничего, кроме JSON.
"""


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def extract_intent(self, query: str) -> Intent:
        if self._enabled():
            try:
                data = await self._chat_json(
                    system_prompt=EXTRACT_PROMPT,
                    user_content=query,
                    max_tokens=350,
                )

                intent = Intent.model_validate(data)

                intent.origin_city = _clean_city(intent.origin_city)
                intent.destination_city = _clean_city(intent.destination_city)
                intent.route_city = _clean_city(
                    intent.route_city or intent.destination_city or intent.origin_city
                )
                intent.city = intent.route_city

                if intent.trip_scope == "unknown":
                    if intent.origin_city and intent.destination_city and intent.origin_city != intent.destination_city:
                        intent.trip_scope = "between_cities"
                    elif intent.route_city:
                        intent.trip_scope = "in_city"

                if not intent.interests:
                    intent.interests = _extract_interests_fallback(query)

                if not intent.route_city:
                    intent = await self._enrich_intent_with_osm(query, intent)

                if intent.route_city:
                    return intent

            except Exception as exc:
                logger.warning("LLM extract_intent fallback due to: %s", exc)

        intent = self._fallback_intent(query)

        if not intent.route_city:
            intent = await self._enrich_intent_with_osm(query, intent)

        return intent

    async def generate_search_phrases(self, intent: Intent, rag_snippets: list[str]) -> list[str]:
        city = intent.route_city or intent.destination_city or intent.city or intent.origin_city or ""

        if not self._enabled():
            return make_search_phrases_fallback(
                query=" ".join(intent.interests) or "",
                city=city,
                interests=intent.interests,
            )

        payload = {
            "city": city,
            "interests": intent.interests,
            "vibe": intent.vibe,
            "pace": intent.pace,
            "rag_snippets": rag_snippets[:3],
        }

        try:
            data = await self._chat_json(
                system_prompt=PHRASES_PROMPT,
                user_content=json.dumps(payload, ensure_ascii=False),
                max_tokens=300,
            )
            phrases = data.get("phrases", [])
            cleaned = [p.strip() for p in phrases if isinstance(p, str) and p.strip()]
            cleaned = self._normalize_generated_phrases(cleaned, city)
            if cleaned:
                return cleaned[:10]
        except Exception as exc:
            logger.warning("LLM generate_search_phrases fallback due to: %s", exc)

        return make_search_phrases_fallback(
            query=" ".join(intent.interests) or "",
            city=city,
            interests=intent.interests,
        )

    async def _chat_json(self, system_prompt: str, user_content: str, max_tokens: int = 300) -> dict[str, Any]:
        if self.settings.ollama_enabled:
            return await self._chat_json_ollama(system_prompt, user_content, max_tokens=max_tokens)
        return await self._chat_json_openai(system_prompt, user_content, max_tokens=max_tokens)

    async def _chat_json_ollama(self, system_prompt: str, user_content: str, max_tokens: int = 300) -> dict[str, Any]:
        prompt = (
            f"{system_prompt}\n\n"
            f"Пользовательский запрос / данные:\n{user_content}\n\n"
            f"Ответь строго одним JSON-объектом."
        )

        body = {
            "model": self.settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0,
                "num_predict": max_tokens,
            },
        }

        url = f"{self.settings.ollama_base_url.rstrip('/')}/api/generate"
        async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()

        raw = data.get("response", "").strip()
        return self._loads_json_safely(raw)

    async def _chat_json_openai(self, system_prompt: str, user_content: str, max_tokens: int = 300) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.openai_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }

        async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        return self._loads_json_safely(content)

    async def _enrich_intent_with_osm(self, query: str, intent: Intent) -> Intent:
        text = _normalize_text(query)

        if not intent.interests:
            intent.interests = _extract_interests_fallback(query)

        origin_candidate, destination_candidate = _extract_origin_destination_candidates(text)

        if origin_candidate and not intent.origin_city:
            resolved = await self._resolve_city_with_nominatim(origin_candidate)
            if resolved:
                intent.origin_city = resolved

        if destination_candidate and not intent.destination_city:
            resolved = await self._resolve_city_with_nominatim(destination_candidate)
            if resolved:
                intent.destination_city = resolved

        if not intent.destination_city and not intent.route_city:
            for cand in _extract_city_candidates(text):
                resolved = await self._resolve_city_with_nominatim(cand)
                if resolved:
                    # если есть origin и resolved совпал с origin, это не destination
                    if intent.origin_city and resolved == intent.origin_city:
                        continue
                    intent.destination_city = resolved
                    break

        if intent.destination_city:
            intent.route_city = intent.destination_city
            intent.city = intent.route_city
            if intent.origin_city and intent.origin_city != intent.destination_city:
                intent.trip_scope = "between_cities"
            else:
                intent.trip_scope = "in_city"

        # ВАЖНО:
        # не подставляем origin как route_city, если в запросе явно была межгородская конструкция
        elif intent.origin_city and not _query_looks_between_cities(text):
            intent.route_city = intent.origin_city
            intent.city = intent.route_city
            intent.trip_scope = "in_city"

        return intent

    async def _resolve_city_with_nominatim(self, fragment: str | None) -> str | None:
        if not fragment:
            return None

        fragment = _clean_city(fragment)
        if not fragment:
            return None

        params = {
            "q": fragment,
            "format": "jsonv2",
            "limit": 5,
            "addressdetails": 1,
            "accept-language": "ru",
        }
        if self.settings.nominatim_email:
            params["email"] = self.settings.nominatim_email

        headers = {
            "User-Agent": self.settings.nominatim_user_agent,
        }

        url = f"{self.settings.nominatim_base_url.rstrip('/')}/search"

        try:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds, headers=headers) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("Nominatim city resolve failed for fragment=%s due to: %s", fragment, exc)
            return None

        for item in data:
            address = item.get("address") or {}
            addresstype = str(item.get("addresstype") or "").lower()
            osm_type = str(item.get("type") or "").lower()
            category = str(item.get("class") or "").lower()

            # Берём только реально городские сущности.
            city_name = (
                address.get("city")
                or address.get("town")
                or address.get("municipality")
                or address.get("village")
            )

            if city_name:
                if addresstype in {"state", "region", "county"}:
                    continue
                if osm_type in {"administrative", "region", "state", "county"} and not address.get("city") and not address.get("town"):
                    continue
                if category == "boundary" and addresstype not in {"city", "town", "municipality", "village"}:
                    continue

                cleaned = _clean_city(str(city_name))
                if cleaned:
                    return cleaned

        # fallback: первая часть display_name только если она похожа на город, а не область
        for item in data:
            display = item.get("display_name", "")
            if not display:
                continue
            first = display.split(",")[0].strip()
            lowered = first.lower()
            if "область" in lowered or "район" in lowered or "край" in lowered or "республика" in lowered:
                continue
            cleaned = _clean_city(first)
            if cleaned:
                return cleaned

        return None

    def _loads_json_safely(self, raw: str) -> dict[str, Any]:
        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(0))

    def _enabled(self) -> bool:
        if not self.settings.llm_enabled:
            return False
        if self.settings.ollama_enabled:
            return True
        return bool(self.settings.openai_api_key)

    def _fallback_intent(self, query: str) -> Intent:
        text = _normalize_text(query)

        origin_city, destination_city = _parse_origin_destination(text)

        if destination_city:
            route_city = destination_city
            trip_scope = "between_cities" if origin_city else "in_city"
        else:
            single_city = _parse_single_city(text)
            route_city = single_city
            destination_city = single_city
            trip_scope = "in_city" if single_city else "unknown"

        interests = _extract_interests_fallback(query)

        vibe = "urban"
        if any(x in text for x in ["бар", "бары", "клуб", "ночная жизнь", "рок"]):
            vibe = "nightlife"
        elif any(x in text for x in ["музей", "искусство", "галерея", "выставка", "культур"]):
            vibe = "culture"
        elif any(x in text for x in ["парк", "природа", "лес", "озеро", "набереж"]):
            vibe = "nature"
        elif any(x in text for x in ["еда", "кафе", "ресторан", "поесть", "покушать"]):
            vibe = "food"

        pace = "moderate"
        if any(x in text for x in ["спокойно", "не спеша", "медленно", "расслабленно"]):
            pace = "slow"
        elif any(x in text for x in ["максимум мест", "насыщенно", "быстро", "плотно"]):
            pace = "intense"

        return Intent(
            city=route_city,
            origin_city=origin_city,
            destination_city=destination_city,
            route_city=route_city,
            trip_scope=trip_scope,
            interests=interests,
            vibe=vibe,
            pace=pace,
        )

    def _normalize_generated_phrases(self, phrases: list[str], city: str) -> list[str]:
        out: list[str] = []
        for phrase in phrases:
            p = phrase.strip()
            if not p:
                continue
            if city and city.lower() not in p.lower():
                p = f"{p}, {city}"
            if p not in out:
                out.append(p)
        return out


def make_search_phrases_fallback(
    query: str,
    city: str,
    interests: list[str] | None = None,
) -> list[str]:
    interests = interests or []
    phrases: list[str] = []

    def add(x: str):
        x = x.strip()
        if x and x not in phrases:
            phrases.append(x)

    if any("искус" in i.lower() or "галере" in i.lower() for i in interests):
        add(f"музей современного искусства, {city}" if city else "музей современного искусства")
        add(f"галерея современного искусства, {city}" if city else "галерея современного искусства")

    if any("музе" in i.lower() for i in interests):
        add(f"музей, {city}" if city else "музей")

    if any("достопримеч" in i.lower() for i in interests):
        add(f"достопримечательности центр, {city}" if city else "достопримечательности центр")

    if any("культур" in i.lower() for i in interests):
        add(f"театр, {city}" if city else "театр")
        add(f"музей истории города, {city}" if city else "музей истории города")

    if any("каф" in i.lower() or "коф" in i.lower() for i in interests):
        add(f"кафе в центре, {city}" if city else "кафе в центре")

    if any("ресторан" in i.lower() or "еда" in i.lower() for i in interests):
        add(f"ресторан локальная кухня, {city}" if city else "ресторан локальная кухня")

    if any("бар" in i.lower() for i in interests):
        add(f"бар с живой музыкой, {city}" if city else "бар с живой музыкой")

    if any("рок" in i.lower() for i in interests):
        add(f"рок-бар, {city}" if city else "рок-бар")

    if any("прогул" in i.lower() or "парк" in i.lower() or "ходь" in i.lower() for i in interests):
        add(f"парк для прогулки, {city}" if city else "парк для прогулки")
        add(f"набережная, {city}" if city else "набережная")

    if not phrases:
        add(f"достопримечательности, {city}" if city else "достопримечательности")
        add(f"музей, {city}" if city else "музей")
        add(f"кафе, {city}" if city else "кафе")
        add(f"центр города, {city}" if city else "центр города")

    return phrases[:12]


def _normalize_text(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clean_city(city: str | None) -> str | None:
    if not city:
        return None

    city = city.strip(" ,.-")
    city = re.sub(r"\s+", " ", city)
    if not city:
        return None

    city_lower = city.lower().replace("ё", "е")
    city_lower = re.sub(r"^(в|во|на|из|по|к|до|от|для|с)\s+", "", city_lower).strip()

    aliases = {
        "питер": "санкт-петербург",
        "спб": "санкт-петербург",
        "санкт петербург": "санкт-петербург",
        "екб": "екатеринбург",
    }
    if city_lower in aliases:
        city_lower = aliases[city_lower]

    words = city_lower.split()
    if words:
        last = words[-1]

        if last.endswith("бурге"):
            last = last[:-1]
        elif last.endswith("граде"):
            last = last[:-1]
        elif last.endswith("ани"):
            last = last[:-1] + "ь"
        elif last.endswith("ах"):
            last = last[:-2] + "ы"
        elif last.endswith("ях"):
            last = last[:-2] + "и"
        elif last.endswith("е") and len(last) > 4:
            if last.endswith("ве"):
                last = last[:-1] + "а"
            elif last.endswith("ре"):
                last = last[:-1] + "а"
            elif last.endswith("де"):
                last = last[:-1]
        words[-1] = last

    city = " ".join(words)

    parts = re.split(r"(\s+|-)", city)
    out: list[str] = []
    for part in parts:
        if not part or part.isspace() or part == "-":
            out.append(part)
        else:
            out.append(part[:1].upper() + part[1:].lower())

    normalized = "".join(out)
    normalized = normalized.replace("Санкт-петербург", "Санкт-Петербург")
    normalized = normalized.replace("Ростов-на-дону", "Ростов-на-Дону")
    return normalized


def _parse_origin_destination(text: str) -> tuple[str | None, str | None]:
    travel_verbs = r"(?:поехать|поеду|еду|съездить|уехать|улететь|отправиться|добраться)"

    patterns = [
        rf"\bиз\s+(?P<origin>[а-яa-z\- ]+?)\s+(?:в|во|на)\s+(?P<destination>[а-яa-z\- ]+?){TAIL_AFTER_CITY}(?:[.,;!?]|$)",
        rf"\b(?:хочу\s+)?{travel_verbs}\s+из\s+(?P<origin>[а-яa-z\- ]+?)\s+(?:в|во|на)\s+(?P<destination>[а-яa-z\- ]+?){TAIL_AFTER_CITY}(?:[.,;!?]|$)",
        rf"\bиз\s+(?P<origin>[а-яa-z\- ]+?)\s+(?:хочу\s+)?{travel_verbs}\s+(?:в|во|на)\s+(?P<destination>[а-яa-z\- ]+?){TAIL_AFTER_CITY}(?:[.,;!?]|$)",
        rf"\b(?:хочу\s+)?{travel_verbs}\s+(?:в|во|на)\s+(?P<destination>[а-яa-z\- ]+?)\s+из\s+(?P<origin>[а-яa-z\- ]+?){TAIL_AFTER_CITY}(?:[.,;!?]|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            origin = _extract_city_fragment(m.group("origin"))
            destination = _extract_city_fragment(m.group("destination"))
            return _clean_city(origin), _clean_city(destination)

    return None, None


def _parse_single_city(text: str) -> str | None:
    travel_verbs = r"(?:поехать|поеду|еду|съездить|уехать|улететь|отправиться|добраться)"
    patterns = [
        rf"\b(?:хочу\s+)?{travel_verbs}\s+(?:в|во|на)\s+([а-яa-z\- ]+?){TAIL_AFTER_CITY}(?:[.,;!?]|$)",
        r"\b(?:маршрут|план|выходные|поездка)\s+(?:в|по)\s+([а-яa-z\- ]+?)(?:[.,;!?]|$)",
        r"\b(?:уже\s+в|буду\s+в|нахожусь\s+в|живу\s+в)\s+([а-яa-z\- ]+?)(?:[.,;!?]|$)",
        r"\bкуда\s+сходить\s+в\s+([а-яa-z\- ]+?)(?:[.,;!?]|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            city = _extract_city_fragment(m.group(1))
            if city:
                return _clean_city(city)

    return None


def _extract_city_candidates(text: str) -> list[str]:
    candidates: list[str] = []

    patterns = [
        rf"\bиз\s+([а-яa-z\- ]+?)(?:\s+(?:в|во|на)\s+|[.,;!?]|$)",
        rf"\b(?:в|во|на)\s+([а-яa-z\- ]+?){TAIL_AFTER_CITY}(?:[.,;!?]|$)",
        r"\b(?:уже\s+в|буду\s+в|нахожусь\s+в|живу\s+в)\s+([а-яa-z\- ]+?)(?:[.,;!?]|$)",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, text):
            cand = _extract_city_fragment(m.group(1))
            cand = _clean_city(cand)
            if cand and cand not in candidates:
                candidates.append(cand)

    return candidates[:8]


def _extract_origin_destination_candidates(text: str) -> tuple[str | None, str | None]:
    travel_verbs = r"(?:поехать|поеду|еду|съездить|уехать|улететь|отправиться|добраться)"

    patterns = [
        rf"\bиз\s+(?P<origin>[а-яa-z\- ]+?)\s+(?:в|во|на)\s+(?P<destination>[а-яa-z\- ]+?){TAIL_AFTER_CITY}(?:[.,;!?]|$)",
        rf"\b(?:хочу\s+)?{travel_verbs}\s+из\s+(?P<origin>[а-яa-z\- ]+?)\s+(?:в|во|на)\s+(?P<destination>[а-яa-z\- ]+?){TAIL_AFTER_CITY}(?:[.,;!?]|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return _extract_city_fragment(m.group("origin")), _extract_city_fragment(m.group("destination"))

    return None, None


def _extract_city_fragment(fragment: str | None) -> str | None:
    if not fragment:
        return None

    fragment = fragment.strip(" ,.-")
    fragment = re.split(
        r"\b("
        r"люблю|хочу|нужен|нужна|интерес|бюджет|"
        r"дней|дня|день|ночей|ночь|суток|"
        r"на\s+\d+\s+дн|на\s+\d+\s+дня|на\s+\d+\s+дней|"
        r"на\s+выходн|"
        r"чтобы|где|и\s+хочу|и\s+люблю|"
        r"посмотреть|увидеть|поесть|покушать|придумай|"
        r"культурную|культурная|программу|достопримечательности|"
        r"вкусно|также|тоже|маршрут|прогулки|ходьба|ходить"
        r")\b",
        fragment,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" ,.-")

    words = fragment.split()
    if not words:
        return None

    return " ".join(words[:4]).strip()


def _extract_interests_fallback(query: str) -> list[str]:
    lowered = _normalize_text(query)
    interests: list[str] = []

    mappings = {
        "рок": "рок-бары",
        "бар": "бары",
        "искус": "современное искусство",
        "музе": "музеи",
        "галере": "галереи",
        "еда": "рестораны",
        "кухн": "рестораны",
        "поесть": "рестораны",
        "покуш": "рестораны",
        "прогул": "прогулки",
        "ходь": "прогулки",
        "ходить": "прогулки",
        "истор": "исторический центр",
        "коф": "кофейни",
        "достопримеч": "достопримечательности",
        "парк": "прогулки",
        "набереж": "прогулки",
        "культур": "культурная программа",
        "театр": "культурная программа",
    }

    for token, val in mappings.items():
        if token in lowered and val not in interests:
            interests.append(val)

    if not interests:
        interests = ["достопримечательности", "кафе", "прогулки"]

    return interests


def _query_looks_between_cities(text: str) -> bool:
    return bool(re.search(r"\bиз\s+[а-яa-z\- ]+?\s+(?:в|во|на)\s+[а-яa-z\- ]+", text))
