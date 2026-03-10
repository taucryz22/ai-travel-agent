from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from typing import Iterable
from urllib.parse import quote_plus

from models import (
    BudgetSummary,
    DayPlan,
    DaySummary,
    Intent,
    Metrics,
    PlaceCandidate,
    PlanRequest,
    PlanResponse,
    Sources,
    Stop,
    Violation,
)
from utils.time_utils import add_minutes, hm_to_minutes

logger = logging.getLogger(__name__)

DAY_START = "10:00"
LATEST_DAY_END = "23:59"

DURATION_BY_CATEGORY = {
    "museum": 90,
    "gallery": 60,
    "bar": 90,
    "cafe": 75,
    "park": 60,
    "landmark": 45,
    "other": 60,
}

BASE_PRICE_BY_CATEGORY = {
    "museum": 800,
    "gallery": 700,
    "bar": 1400,
    "cafe": 1100,
    "park": 200,
    "landmark": 300,
    "other": 600,
}

TRAVEL_MODE_LABELS = {
    "walking": "Пешком",
    "transit": "Общественный транспорт",
    "driving": "На такси",
}

TRANSPORT_COST_BY_MODE = {
    "walking": 0,
    "transit": 150,
    "driving": 700,
}

SLOT_CONFIG = [
    {"label": "morning", "focus": "культурное начало дня", "preferred": ["museum", "gallery", "landmark"]},
    {"label": "lunch", "focus": "обед или кофе-пауза", "preferred": ["cafe"]},
    {"label": "afternoon", "focus": "дневная прогулка или искусство", "preferred": ["gallery", "park", "landmark", "museum"]},
    {"label": "evening", "focus": "вечерняя точка с атмосферой", "preferred": ["bar", "cafe"]},
]

CATEGORY_STYLE_LABELS = {
    "museum": "Культурный день",
    "gallery": "Современное искусство",
    "bar": "Вечерний городской ритм",
    "cafe": "Гастрономический маршрут",
    "park": "Спокойная прогулка",
    "landmark": "Классический городской маршрут",
    "other": "Смешанный маршрут",
}

CATEGORY_THEME_LABELS = {
    "museum": "Музеи и история",
    "gallery": "Искусство и выставки",
    "bar": "Атмосфера и вечер",
    "cafe": "Еда и отдых",
    "park": "Прогулки и воздух",
    "landmark": "Знаковые места",
    "other": "Разные впечатления",
}


class PlannerService:
    DISTANCE_FILTER_TOP_N = 4

    def __init__(self, routing_service):
        self.routing_service = routing_service

    async def build_plan(
        self,
        request: PlanRequest,
        city: str,
        candidates: list[PlaceCandidate],
        sources: Sources,
        intent: Intent | None = None,
    ) -> PlanResponse:
        logger.info("Planner start: raw_candidates=%s", len(candidates))

        filtered = self._dedupe_places(candidates)
        filtered = self._exclude_places(filtered, request.avoid_place_names)
        filtered = self._prepare_budget_order(filtered, request)
        grouped = self._group_places(filtered)

        days: list[DayPlan] = []
        violations: list[Violation] = []
        total_travel = 0
        used_keys: set[str] = set()

        total_estimated_spend = 0
        daily_budget = request.budget / max(request.days, 1) if request.days else request.budget

        for day_idx in range(request.days):
            remaining_total_budget = max(0, request.budget - total_estimated_spend)
            remaining_days = max(1, request.days - day_idx)
            target_day_budget = max(daily_budget, remaining_total_budget / remaining_days)

            selected = await self._build_day(
                grouped=grouped,
                request=request,
                used_keys=used_keys,
                violations=violations,
                intent=intent,
                target_day_budget=target_day_budget,
                remaining_total_budget=remaining_total_budget,
            )

            if not selected:
                logger.info("Planner stopped at day=%s because no stops left", day_idx + 1)
                break

            day_travel = sum(stop.travel_from_prev_min for stop in selected)
            day_distance = round(sum(stop.travel_from_prev_km for stop in selected), 2)
            total_travel += day_travel

            day_route_url = self._day_route_url(selected)
            day_summary = self._build_day_summary(selected, day_travel, day_distance)

            days.append(
                DayPlan(
                    title=f"День {day_idx + 1}",
                    day_route_url=day_route_url,
                    stops=selected,
                    summary=day_summary,
                )
            )
            total_estimated_spend += sum(stop.price_estimate_rub for stop in selected)

        estimated_total = sum(stop.price_estimate_rub for day in days for stop in day.stops)
        budget_status = self._budget_status(request.budget, estimated_total)
        budget_delta = request.budget - estimated_total

        return PlanResponse(
            city=city,
            request=request,
            days=days,
            budget_summary=BudgetSummary(
                budget_total=request.budget,
                estimated_total=estimated_total,
                currency="RUB",
                notes="Оценка, не является фактической ценой.",
                status=budget_status,
                delta_rub=budget_delta,
            ),
            metrics=Metrics(total_travel_min=total_travel, violations=violations),
            sources=sources,
        )

    def _group_places(self, places: list[PlaceCandidate]) -> dict[str, list[PlaceCandidate]]:
        grouped: dict[str, list[PlaceCandidate]] = defaultdict(list)
        for place in places:
            grouped[place.category].append(place)
        return grouped

    async def _build_day(
        self,
        grouped: dict[str, list[PlaceCandidate]],
        request: PlanRequest,
        used_keys: set[str],
        violations: list[Violation],
        intent: Intent | None,
        target_day_budget: float,
        remaining_total_budget: float,
    ) -> list[Stop]:
        current_time = DAY_START
        previous: PlaceCandidate | None = None
        stops: list[Stop] = []
        current_spend = 0
        picked_categories: Counter[str] = Counter()
        day_cluster: tuple[float, float] | None = None

        for slot in SLOT_CONFIG:
            options = self._collect_slot_options(grouped, slot["preferred"], used_keys)
            if not options:
                continue

            chosen, travel_min, travel_km, travel_mode, score, why = await self._choose_best_candidate(
                previous=previous,
                options=options,
                slot_categories=slot["preferred"],
                current_time=current_time,
                current_spend=current_spend,
                request=request,
                intent=intent,
                picked_categories=picked_categories,
                day_cluster=day_cluster,
                target_day_budget=target_day_budget,
                remaining_total_budget=remaining_total_budget,
                stops_so_far=len(stops),
            )

            if chosen is None:
                continue

            duration = DURATION_BY_CATEGORY.get(chosen.category, 60)
            visit_start = add_minutes(current_time, travel_min) if previous is not None else current_time
            visit_end = add_minutes(visit_start, duration)

            if hm_to_minutes(visit_end) > hm_to_minutes(LATEST_DAY_END):
                violations.append(
                    Violation(
                        type="day_overflow",
                        value_min=hm_to_minutes(visit_end) - hm_to_minutes(LATEST_DAY_END),
                        note=f"{chosen.name} не добавлена: маршрут уходил бы слишком поздно",
                    )
                )
                continue

            point_cost = self._price_for_place(chosen, current_spend, target_day_budget, len(stops))
            transport_cost = TRANSPORT_COST_BY_MODE.get(travel_mode or "walking", 0) if previous else 0
            total_stop_cost = point_cost + transport_cost

            if current_spend + total_stop_cost > remaining_total_budget:
                continue

            if previous is None:
                route_url = self._point_route_url(chosen.lat, chosen.lon, chosen.address)
            else:
                route_url = self._segment_route_url(previous, chosen)

            stop = Stop(
                start=visit_start,
                end=visit_end,
                name=chosen.name,
                address=chosen.address,
                lat=chosen.lat,
                lon=chosen.lon,
                travel_from_prev_min=travel_min if previous else 0,
                travel_from_prev_km=travel_km if previous else 0.0,
                visit_duration_min=duration,
                travel_mode_from_prev=travel_mode if previous else None,
                travel_mode_label=TRAVEL_MODE_LABELS.get(travel_mode) if previous and travel_mode else None,
                open_status=self._open_status(chosen, visit_start),
                route_to_url=route_url,
                category=chosen.category,
                price_estimate_rub=total_stop_cost,
                score=round(score, 2),
                why_selected=why,
                rating=chosen.rating,
                reviews_count=chosen.reviews_count,
            )

            stops.append(stop)
            used_keys.add(self._place_key(chosen))
            previous = chosen
            current_time = stop.end
            current_spend += stop.price_estimate_rub
            picked_categories[chosen.category] += 1
            day_cluster = self._update_cluster(day_cluster, chosen)

        current_spend = self._upgrade_stops_to_use_budget(
            stops=stops,
            current_spend=current_spend,
            target_day_budget=target_day_budget,
            remaining_total_budget=remaining_total_budget,
        )

        return stops

    def _collect_slot_options(
        self,
        grouped: dict[str, list[PlaceCandidate]],
        preferred_categories: list[str],
        used_keys: set[str],
    ) -> list[PlaceCandidate]:
        options: list[PlaceCandidate] = []
        for category in preferred_categories:
            for place in grouped.get(category, []):
                if self._place_key(place) in used_keys:
                    continue
                options.append(place)
        return options

    async def _choose_best_candidate(
        self,
        previous: PlaceCandidate | None,
        options: list[PlaceCandidate],
        slot_categories: list[str],
        current_time: str,
        current_spend: int,
        request: PlanRequest,
        intent: Intent | None,
        picked_categories: Counter[str],
        day_cluster: tuple[float, float] | None,
        target_day_budget: float,
        remaining_total_budget: float,
        stops_so_far: int,
    ) -> tuple[PlaceCandidate | None, int, float, str | None, float, list[str]]:
        best_place: PlaceCandidate | None = None
        best_minutes = 0
        best_distance_km = 0.0
        best_mode: str | None = None
        best_score = -10**9
        best_why: list[str] = []

        narrowed_options = self._distance_prefilter(
            previous=previous,
            options=options,
            intent=intent,
            current_spend=current_spend,
            target_day_budget=target_day_budget,
        )

        for place in narrowed_options:
            travel_min = 0
            travel_km = 0.0
            travel_mode = None

            if previous is not None:
                travel_min, travel_km, travel_mode = await self._choose_travel_option(
                    previous=previous,
                    current=place,
                    request=request,
                    intent=intent,
                    current_spend=current_spend,
                    target_day_budget=target_day_budget,
                    remaining_total_budget=remaining_total_budget,
                )

            score, why = self._score_candidate(
                place=place,
                slot_categories=slot_categories,
                travel_min=travel_min,
                travel_mode=travel_mode,
                current_time=current_time,
                current_spend=current_spend,
                request=request,
                intent=intent,
                has_previous=previous is not None,
                picked_categories=picked_categories,
                day_cluster=day_cluster,
                target_day_budget=target_day_budget,
                stops_so_far=stops_so_far,
                travel_km=travel_km,
            )

            if score > best_score:
                best_place = place
                best_minutes = travel_min
                best_distance_km = travel_km
                best_mode = travel_mode
                best_score = score
                best_why = why

        return best_place, best_minutes, best_distance_km, best_mode, best_score, best_why

    def _distance_prefilter(
        self,
        previous: PlaceCandidate | None,
        options: list[PlaceCandidate],
        intent: Intent | None,
        current_spend: int,
        target_day_budget: float,
    ) -> list[PlaceCandidate]:
        if previous is None or len(options) <= self.DISTANCE_FILTER_TOP_N:
            return options

        likes_walking = self._likes_walking(intent)
        walking_soft_limit_km, walking_hard_limit_km = self._walking_limits_km(intent)

        scored: list[tuple[float, PlaceCandidate]] = []
        for place in options:
            direct_km = self._haversine_km(previous.lat, previous.lon, place.lat, place.lon)

            distance_score = direct_km

            if direct_km > 8:
                distance_score += 10
            elif direct_km > 5:
                distance_score += 4

            if likes_walking and direct_km <= walking_soft_limit_km:
                distance_score -= 0.8

            if likes_walking and direct_km > walking_hard_limit_km:
                distance_score += 2.5

            # если бюджет ещё не добираем — слегка допускаем более дорогие и не самые близкие точки
            usage_ratio = current_spend / max(target_day_budget, 1)
            if usage_ratio < 0.6 and place.category in {"cafe", "bar", "museum"}:
                distance_score -= 0.25

            scored.append((distance_score, place))

        scored.sort(key=lambda x: x[0])
        return [place for _, place in scored[: self.DISTANCE_FILTER_TOP_N]]

    async def _choose_travel_option(
        self,
        previous: PlaceCandidate,
        current: PlaceCandidate,
        request: PlanRequest,
        intent: Intent | None,
        current_spend: int,
        target_day_budget: float,
        remaining_total_budget: float,
    ) -> tuple[int, float, str]:
        origin = (previous.lat, previous.lon)
        destination = (current.lat, current.lon)

        if request.mode in {"walking", "transit", "driving"}:
            info = await self.routing_service.travel_info(origin, destination, request.mode)
            return int(info["minutes"]), float(info["distance_km"]), request.mode

        direct_km = self._haversine_km(previous.lat, previous.lon, current.lat, current.lon)
        likes_walking = self._likes_walking(intent)
        walking_soft_limit_km, walking_hard_limit_km = self._walking_limits_km(intent)

        # очень близко — не дёргаем лишний ORS
        if direct_km <= 0.45:
            walking_km = round(direct_km * 1.2, 2)
            walking_min = max(5, int(round((walking_km / 4.8) * 60)))
            return walking_min, walking_km, "walking"

        options = await self.routing_service.travel_options(
            origin,
            destination,
            allowed_modes=["walking", "transit", "driving"],
        )

        walking = options.get("walking", {"minutes": 999, "distance_km": 0.0})
        transit = options.get("transit", {"minutes": 999, "distance_km": 0.0})
        driving = options.get("driving", {"minutes": 999, "distance_km": 0.0})

        walking_min = int(walking["minutes"])
        transit_min = int(transit["minutes"])
        driving_min = int(driving["minutes"])
        driving_km = float(driving["distance_km"] or transit["distance_km"] or walking["distance_km"] or 0.0)

        relaxed = bool(intent and intent.pace in {"slow", "relaxed"})
        intense = bool(intent and intent.pace == "intense")
        projected_ratio = current_spend / max(target_day_budget, 1)

        # 1. Совсем близко — пешком
        if direct_km <= 1.2 or walking_min <= 15:
            return walking_min, float(walking["distance_km"]), "walking"

        # 2. Любит прогулки — расширяем диапазон пеших переходов, но не безлимитно
        if likes_walking and direct_km <= walking_soft_limit_km and walking_min <= 28:
            return walking_min, float(walking["distance_km"]), "walking"

        if likes_walking and relaxed and direct_km <= walking_hard_limit_km and walking_min <= 40:
            if transit_min >= walking_min - 3:
                return walking_min, float(walking["distance_km"]), "walking"

        # 3. Дальше жёсткого лимита пешком не отправляем
        walking_allowed = direct_km <= walking_hard_limit_km and walking_min <= (45 if relaxed else 35)

        # 4. Средняя дистанция — чаще транспорт
        if direct_km <= 4.5 or walking_min <= 35:
            if walking_allowed and likes_walking and walking_min <= transit_min + 4:
                return walking_min, float(walking["distance_km"]), "walking"
            if transit_min < 999:
                return transit_min, float(transit["distance_km"]), "transit"
            if walking_allowed:
                return walking_min, float(walking["distance_km"]), "walking"

        # 5. Дальняя дистанция — транспорт или такси
        can_upgrade_to_driving = (
            projected_ratio < 0.92
            and current_spend + TRANSPORT_COST_BY_MODE["driving"] <= remaining_total_budget
        )

        if can_upgrade_to_driving and driving_min <= transit_min - 8:
            return driving_min, float(driving["distance_km"]), "driving"

        if intense and can_upgrade_to_driving and driving_min < transit_min:
            return driving_min, float(driving["distance_km"]), "driving"

        if transit_min < 999:
            return transit_min, float(transit["distance_km"]), "transit"

        if walking_allowed:
            return walking_min, float(walking["distance_km"]), "walking"

        best_mode = min(options, key=lambda m: int(options[m]["minutes"]))
        return int(options[best_mode]["minutes"]), float(options[best_mode]["distance_km"]), best_mode

    def _score_candidate(
        self,
        place: PlaceCandidate,
        slot_categories: list[str],
        travel_min: int,
        travel_mode: str | None,
        current_time: str,
        current_spend: int,
        request: PlanRequest,
        intent: Intent | None,
        has_previous: bool,
        picked_categories: Counter[str],
        day_cluster: tuple[float, float] | None,
        target_day_budget: float,
        stops_so_far: int,
        travel_km: float = 0.0,
    ) -> tuple[float, list[str]]:
        score = 0.0
        why: list[str] = []

        if place.category in slot_categories:
            score += 35
            why.append(f"Подходит под слот дня: {place.category}")

        category_conf = max(0.0, min(1.0, place.category_confidence))
        score += category_conf * 8

        open_status = self._open_status(place, current_time)
        if open_status == "open":
            score += 18
            why.append("Работает в выбранное время")
        elif open_status == "unknown":
            score += 5
            why.append("Статус открытия не подтверждён")
        else:
            score -= 45
            why.append("Есть риск, что место закрыто")

        likes_walking = self._likes_walking(intent)

        if has_previous:
            if travel_min <= 10:
                score += 22
                why.append(f"Очень близко к предыдущей точке: {travel_min} мин")
            elif travel_min <= 20:
                score += 14
                why.append(f"Удобный переезд: {travel_min} мин")
            elif travel_min <= 35:
                score += 6
                why.append(f"Переезд допустим: {travel_min} мин")
            elif travel_min <= 60:
                score -= 2
                why.append(f"Нужно заложить заметный переезд: {travel_min} мин")
            elif travel_min <= 90:
                score -= 8
                why.append(f"Маршрут включает длинный переезд: {travel_min} мин")
            else:
                score -= min(24, 10 + (travel_min - 90) * 0.12)
                why.append(f"Долгая дорога между точками: {travel_min} мин")

            if travel_mode:
                why.append(f"Способ добраться: {TRAVEL_MODE_LABELS.get(travel_mode, travel_mode)}")
                if request.mode == "smart" and travel_mode == "walking":
                    score += 4
                if request.mode == "smart" and travel_mode == "transit":
                    score += 2
                if request.mode == "smart" and travel_mode == "driving":
                    score += 3
                    if current_spend < target_day_budget * 0.85:
                        score += 4
                        why.append("Такси помогает комфортно использовать доступный бюджет")

                if likes_walking and travel_mode == "walking":
                    if travel_km <= self._walking_limits_km(intent)[0]:
                        score += 5
                        why.append("Хорошо подходит под любовь к прогулкам")
                    elif travel_km > self._walking_limits_km(intent)[1]:
                        score -= 12
                        why.append("Даже с любовью к прогулкам переход слишком длинный пешком")
        else:
            score += 12
            why.append("Подходит как стартовая точка дня")

        if day_cluster is not None:
            cluster_penalty = self._cluster_penalty(day_cluster, place)
            score -= cluster_penalty
            if cluster_penalty >= 8:
                why.append("Сильно выбивается из основной зоны дня")
            elif cluster_penalty >= 3:
                why.append("Слегка уводит маршрут в сторону")

        repeat_count = picked_categories.get(place.category, 0)
        if repeat_count == 1:
            score -= 5
            why.append("Добавляет вариативность слабее, чем новая категория")
        elif repeat_count >= 2:
            score -= 11
            why.append("Категория уже повторялась несколько раз")

        price = self._base_price_for_place(place)
        projected_spend = current_spend + price

        if target_day_budget > 0:
            ratio = projected_spend / target_day_budget
            if 0.65 <= ratio <= 1.0:
                score += 12
                why.append("Хорошо помогает приблизиться к дневному бюджету")
            elif 0.45 <= ratio < 0.65:
                score += 6
                why.append("Бюджет пока используется умеренно")
            elif ratio < 0.45:
                score -= 2
                why.append("Маршрут пока расходует бюджет слишком медленно")
            elif ratio <= 1.08:
                score -= 6
                why.append("Почти упирается в бюджет")
            else:
                score -= 18
                why.append("Увеличивает риск превышения бюджета")

        text_blob = " ".join(
            [
                place.name.lower(),
                place.address.lower(),
                " ".join(place.categories_raw).lower(),
                (place.source_query or "").lower(),
            ]
        )

        if intent:
            intent_hits = 0
            for interest in intent.interests:
                tokens = self._meaningful_tokens(interest)
                if any(token in text_blob for token in tokens):
                    intent_hits += 1
            if intent_hits > 0:
                score += min(22, intent_hits * 7)
                why.append("Хорошо совпадает с интересами пользователя")

            if intent.pace in {"slow", "relaxed"} and place.category in {"park", "cafe", "gallery"}:
                score += 4
                why.append("Подходит под спокойный темп")

            if likes_walking and place.category in {"park", "landmark", "gallery"}:
                score += 3
                why.append("Подходит для прогулочного сценария")

        if place.rating is not None:
            score += max(0.0, min(place.rating, 5.0)) * 2.0
            why.append(f"Есть хороший рейтинг: {place.rating:.1f}")

        if place.reviews_count is not None:
            score += min(8.0, math.log10(max(place.reviews_count, 1)) * 3.0)
            why.append("Есть заметный объём отзывов")

        if stops_so_far >= 2 and current_spend < target_day_budget * 0.6:
            if place.category in {"bar", "cafe", "museum"}:
                score += 5
                why.append("Помогает довести дневной бюджет до более комфортного уровня")

        return score, self._dedupe_preserve_order(why)[:6]

    def _likes_walking(self, intent: Intent | None) -> bool:
        if not intent:
            return False

        hay = " ".join(intent.interests).lower()
        walking_tokens = [
            "прогул",
            "ходьб",
            "ходить",
            "пеш",
            "погуля",
            "набереж",
            "walk",
            "walking",
        ]
        return any(token in hay for token in walking_tokens)

    def _walking_limits_km(self, intent: Intent | None) -> tuple[float, float]:
        likes_walking = self._likes_walking(intent)
        relaxed = bool(intent and intent.pace in {"slow", "relaxed"})
        intense = bool(intent and intent.pace == "intense")

        if likes_walking and relaxed:
            return 2.3, 3.8
        if likes_walking:
            return 1.8, 3.0
        if intense:
            return 0.9, 1.7
        return 1.2, 2.2

    def _open_status(self, place: PlaceCandidate, current_time: str) -> str:
        if place.hours_intervals:
            status = self._open_status_from_intervals(place.hours_intervals, current_time)
            if status != "unknown":
                return status

        if not place.hours_text:
            return "unknown"

        hours = place.hours_text.lower()
        if "круглосуточ" in hours:
            return "open"

        match = re.search(r"(\d{2}:\d{2})\D+(\d{2}:\d{2})", place.hours_text)
        if not match:
            return "unknown"

        start, end = match.groups()
        return self._is_open_in_range(current_time, start, end)

    def _open_status_from_intervals(self, intervals: list[dict], current_time: str) -> str:
        now = hm_to_minutes(current_time)
        found = False

        for item in intervals:
            candidate_ranges: list[tuple[str, str]] = []
            if isinstance(item, dict):
                start = item.get("from") or item.get("start") or item.get("opening")
                end = item.get("to") or item.get("end") or item.get("closing")
                if isinstance(start, str) and isinstance(end, str):
                    candidate_ranges.append((start[:5], end[:5]))

                nested = item.get("Intervals") or item.get("intervals") or []
                for sub in nested:
                    if isinstance(sub, dict):
                        sub_start = sub.get("from") or sub.get("start")
                        sub_end = sub.get("to") or sub.get("end")
                        if isinstance(sub_start, str) and isinstance(sub_end, str):
                            candidate_ranges.append((sub_start[:5], sub_end[:5]))

            for start, end in candidate_ranges:
                found = True
                if self._range_contains(now, hm_to_minutes(start), hm_to_minutes(end)):
                    return "open"

        if found:
            return "closed"
        return "unknown"

    def _is_open_in_range(self, current_time: str, start: str, end: str) -> str:
        now = hm_to_minutes(current_time)
        start_min = hm_to_minutes(start)
        end_min = hm_to_minutes(end)
        return "open" if self._range_contains(now, start_min, end_min) else "closed"

    def _range_contains(self, now: int, start_min: int, end_min: int) -> bool:
        if end_min < start_min:
            return now >= start_min or now <= end_min
        return start_min <= now <= end_min

    def _point_route_url(self, lat: float, lon: float, fallback_text: str) -> str:
        if lat is None or lon is None:
            return f"https://yandex.ru/maps/?text={quote_plus(fallback_text)}"
        return f"https://yandex.ru/maps/?rtext=~{lat},{lon}"

    def _segment_route_url(self, previous: PlaceCandidate, current: PlaceCandidate) -> str:
        return f"https://yandex.ru/maps/?rtext={previous.lat},{previous.lon}~{current.lat},{current.lon}"

    def _day_route_url(self, stops: list[Stop]) -> str:
        coords = "".join([f"~{stop.lat},{stop.lon}" for stop in stops])
        if not coords:
            return "https://yandex.ru/maps/"
        return f"https://yandex.ru/maps/?rtext={coords}"

    def _dedupe_places(self, places: list[PlaceCandidate]) -> list[PlaceCandidate]:
        seen: set[tuple[str, str, int, int]] = set()
        out: list[PlaceCandidate] = []

        for place in places:
            key = (
                place.name.lower().strip(),
                place.address.lower().strip(),
                round(place.lat, 3),
                round(place.lon, 3),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(place)

        return out

    def _exclude_places(self, places: list[PlaceCandidate], avoid_place_names: list[str]) -> list[PlaceCandidate]:
        banned = {name.strip().lower() for name in avoid_place_names if name.strip()}
        if not banned:
            return places
        return [place for place in places if place.name.strip().lower() not in banned]

    def _prepare_budget_order(self, places: list[PlaceCandidate], request: PlanRequest) -> list[PlaceCandidate]:
        rough_daily_budget = request.budget / max(request.days, 1)

        def sort_key(p: PlaceCandidate) -> tuple[int, float]:
            base_price = self._base_price_for_place(p)
            return (
                -base_price if rough_daily_budget >= 5000 else base_price,
                -(p.rating or 0.0),
            )

        return sorted(places, key=sort_key)

    def _base_price_for_place(self, place: PlaceCandidate) -> int:
        return BASE_PRICE_BY_CATEGORY.get(place.category, 600)

    def _price_for_place(
        self,
        place: PlaceCandidate,
        current_spend: int,
        target_day_budget: float,
        stop_index: int,
    ) -> int:
        base = self._base_price_for_place(place)

        if target_day_budget <= 0:
            return base

        usage_ratio = current_spend / max(target_day_budget, 1)

        if place.category == "cafe":
            if usage_ratio < 0.55:
                return base + 900
            if usage_ratio < 0.8:
                return base + 500
            return base

        if place.category == "bar":
            if usage_ratio < 0.75:
                return base + 600
            return base

        if place.category in {"museum", "gallery"}:
            if usage_ratio < 0.7:
                return base + 300
            return base

        if place.category == "landmark":
            if usage_ratio < 0.5 and stop_index >= 1:
                return base + 200
            return base

        return base

    def _upgrade_stops_to_use_budget(
        self,
        stops: list[Stop],
        current_spend: int,
        target_day_budget: float,
        remaining_total_budget: float,
    ) -> int:
        if not stops:
            return current_spend

        hard_limit = int(min(target_day_budget, remaining_total_budget))
        if current_spend >= hard_limit:
            return current_spend

        upgrade_priority = ["cafe", "bar", "museum", "gallery", "landmark"]

        for category in upgrade_priority:
            for stop in stops:
                if stop.category != category:
                    continue

                bump = self._upgrade_increment(stop.category, stop)
                if bump <= 0:
                    continue

                if current_spend + bump <= hard_limit:
                    stop.price_estimate_rub += bump
                    current_spend += bump
                    stop.why_selected = self._dedupe_preserve_order(
                        stop.why_selected + ["Улучшен уровень комфорта, чтобы лучше использовать бюджет дня"]
                    )[:6]

        return current_spend

    def _upgrade_increment(self, category: str, stop: Stop) -> int:
        if category == "cafe":
            return 700
        if category == "bar":
            return 500
        if category in {"museum", "gallery"}:
            return 300
        if category == "landmark":
            return 200
        return 0

    def _transport_cost(self, request: PlanRequest) -> int:
        if request.mode == "walking":
            return 0
        if request.mode == "transit":
            return 250 * request.days
        if request.mode == "driving":
            return 900 * request.days
        return 450 * request.days

    def _budget_status(self, budget_total: int, estimated_total: int) -> str:
        if budget_total <= 0:
            return "over_budget" if estimated_total > 0 else "ok"
        if estimated_total <= budget_total:
            return "ok"
        if estimated_total <= budget_total * 1.2:
            return "near_limit"
        return "over_budget"

    def _build_day_summary(self, stops: list[Stop], total_travel_min: int, total_travel_km: float) -> DaySummary:
        estimated_day_budget = sum(stop.price_estimate_rub for stop in stops)
        total_visit_min = sum(stop.visit_duration_min for stop in stops)
        dominant_category = self._dominant_category(stops)
        area_label = self._area_label(stops)

        return DaySummary(
            stops_count=len(stops),
            total_travel_min=total_travel_min,
            total_travel_km=round(total_travel_km, 2),
            total_visit_min=total_visit_min,
            estimated_day_budget_rub=estimated_day_budget,
            focus=self._focus_label(stops),
            area_label=area_label,
            style_label=CATEGORY_STYLE_LABELS.get(dominant_category, CATEGORY_STYLE_LABELS["other"]),
            theme_label=CATEGORY_THEME_LABELS.get(dominant_category, CATEGORY_THEME_LABELS["other"]),
        )

    def _focus_label(self, stops: list[Stop]) -> str:
        categories = [stop.category for stop in stops]
        if any(cat in categories for cat in ["museum", "gallery"]):
            return "искусство и город"
        if "bar" in categories:
            return "вечер и атмосфера"
        if "park" in categories:
            return "прогулка и спокойный ритм"
        return "смешанный городской маршрут"

    def _dominant_category(self, stops: list[Stop]) -> str:
        if not stops:
            return "other"
        counts = Counter(stop.category for stop in stops)
        return counts.most_common(1)[0][0]

    def _area_label(self, stops: list[Stop]) -> str:
        if not stops:
            return "Смешанный район"
        avg_lat = sum(stop.lat for stop in stops) / len(stops)
        avg_lon = sum(stop.lon for stop in stops) / len(stops)
        lat_span = max(stop.lat for stop in stops) - min(stop.lat for stop in stops)
        lon_span = max(stop.lon for stop in stops) - min(stop.lon for stop in stops)

        compact = max(lat_span, lon_span) < 0.06
        lat_name = "северная часть" if avg_lat > 59.94 else "южная часть" if avg_lat < 59.91 else "центральная часть"
        lon_name = "запад" if avg_lon < 30.29 else "восток" if avg_lon > 30.35 else "центр"

        if compact:
            return f"{lat_name}, {lon_name}"
        return "Маршрут по нескольким районам"

    def _update_cluster(self, day_cluster: tuple[float, float] | None, place: PlaceCandidate) -> tuple[float, float]:
        if day_cluster is None:
            return (place.lat, place.lon)
        return ((day_cluster[0] + place.lat) / 2, (day_cluster[1] + place.lon) / 2)

    def _cluster_penalty(self, day_cluster: tuple[float, float], place: PlaceCandidate) -> float:
        dist = math.sqrt((day_cluster[0] - place.lat) ** 2 + (day_cluster[1] - place.lon) ** 2)
        if dist <= 0.015:
            return 0.0
        if dist <= 0.03:
            return 2.5
        if dist <= 0.06:
            return 6.0
        return 11.0

    def _meaningful_tokens(self, text: str) -> list[str]:
        return [token for token in re.findall(r"[а-яa-z0-9\-]+", text.lower()) if len(token) >= 3]

    def _dedupe_preserve_order(self, items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    def _place_key(self, place: PlaceCandidate) -> str:
        return f"{place.name.strip().lower()}::{place.address.strip().lower()}::{round(place.lat, 4)}::{round(place.lon, 4)}"

    def _haversine_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)

        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return r * c
