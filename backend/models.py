from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Mode = Literal["smart", "walking", "transit", "driving"]
Category = Literal["museum", "gallery", "bar", "cafe", "park", "landmark", "other"]
OpenStatus = Literal["open", "closed", "unknown"]
BudgetStatus = Literal["ok", "near_limit", "over_budget"]
TravelMode = Literal["walking", "transit", "driving"]
TripScope = Literal["in_city", "between_cities", "unknown"]


class PlanRequest(BaseModel):
    query: str = Field(min_length=3)
    days: int = Field(ge=1, le=5)
    budget: int = Field(ge=0)
    mode: Mode = "smart"
    avoid_place_names: list[str] = Field(default_factory=list)


class Intent(BaseModel):
    city: str | None = None
    origin_city: str | None = None
    destination_city: str | None = None
    route_city: str | None = None
    trip_scope: TripScope = "unknown"
    interests: list[str] = Field(default_factory=list)
    vibe: str = "balanced"
    pace: str = "moderate"


class PlaceCandidate(BaseModel):
    name: str
    address: str
    lat: float
    lon: float
    category: Category = "other"
    categories_raw: list[str] = Field(default_factory=list)
    hours_text: str | None = None
    hours_intervals: list[dict] = Field(default_factory=list)
    source_query: str | None = None
    rating: float | None = None
    reviews_count: int | None = None
    category_confidence: float = 0.6


class Stop(BaseModel):
    start: str
    end: str
    name: str
    address: str
    lat: float
    lon: float

    travel_from_prev_min: int
    travel_from_prev_km: float = 0.0

    visit_duration_min: int = 0
    travel_mode_from_prev: TravelMode | None = None
    travel_mode_label: str | None = None
    open_status: OpenStatus
    route_to_url: str
    category: Category
    price_estimate_rub: int
    score: float = 0.0
    why_selected: list[str] = Field(default_factory=list)
    rating: float | None = None
    reviews_count: int | None = None


class DaySummary(BaseModel):
    stops_count: int
    total_travel_min: int
    total_travel_km: float = 0.0
    total_visit_min: int = 0
    estimated_day_budget_rub: int
    focus: str
    area_label: str = "Смешанный район"
    style_label: str = "Сбалансированный маршрут"
    theme_label: str = "Разные впечатления"


class DayPlan(BaseModel):
    title: str
    day_route_url: str
    stops: list[Stop]
    summary: DaySummary | None = None


class Violation(BaseModel):
    type: str
    value_min: int
    note: str


class BudgetSummary(BaseModel):
    budget_total: int
    estimated_total: int
    currency: str = "RUB"
    notes: str = "Оценка"
    status: BudgetStatus = "ok"
    delta_rub: int = 0


class Metrics(BaseModel):
    total_travel_min: int
    violations: list[Violation]


class Sources(BaseModel):
    wikivoyage_page: str | None = None
    rag_snippets: list[str] = Field(default_factory=list)
    generated_search_phrases: list[str] = Field(default_factory=list)


class PlanResponse(BaseModel):
    city: str
    request: PlanRequest
    days: list[DayPlan]
    budget_summary: BudgetSummary
    metrics: Metrics
    sources: Sources


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ErrorResponse(BaseModel):
    detail: str
