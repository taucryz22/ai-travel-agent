from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import ErrorResponse, HealthResponse, PlanRequest, PlanResponse, Sources
from services.llm_client import LLMClient, make_search_phrases_fallback
from services.openrouteservice_routing import OpenRouteServiceRoutingService
from services.osm_places import OSMPlacesService
from services.planner import PlannerService
from services.rag_wikivoyage import RagWikivoyageService
from services.wikivoyage_ingest import WikivoyageIngestService
from settings import get_settings
from utils.cache import TTLCache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    places_cache = TTLCache(ttl_seconds=settings.cache_ttl_seconds)
    routing_cache = TTLCache(ttl_seconds=settings.cache_ttl_seconds)

    app.state.settings = settings
    app.state.llm = LLMClient(settings)
    app.state.wikivoyage = WikivoyageIngestService(settings)
    app.state.rag = RagWikivoyageService()
    app.state.places = OSMPlacesService(settings, places_cache)
    app.state.routing = OpenRouteServiceRoutingService(settings, routing_cache)
    app.state.planner = PlannerService(app.state.routing)

    logger.info(
        "App started. mock_mode=%s llm_enabled=%s ollama_enabled=%s ollama_model=%s",
        settings.mock_mode,
        settings.llm_enabled,
        settings.ollama_enabled,
        settings.ollama_model,
    )
    yield


app = FastAPI(title="AI Travel Agent", version="1.5.0", lifespan=lifespan)
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/api/plan", response_model=PlanResponse, responses={400: {"model": ErrorResponse}})
async def create_plan(payload: PlanRequest) -> PlanResponse:
    settings = app.state.settings
    llm: LLMClient = app.state.llm
    wikivoyage = app.state.wikivoyage
    rag = app.state.rag
    places = app.state.places
    planner = app.state.planner

    intent = await llm.extract_intent(payload.query)
    route_city = intent.route_city or intent.destination_city or intent.city or intent.origin_city

    if not route_city:
        raise HTTPException(
            status_code=400,
            detail="Не удалось определить город маршрута. Укажи, пожалуйста, город назначения или город, в котором нужен маршрут.",
        )

    intent.city = route_city
    intent.route_city = route_city

    logger.info(
        "Intent extracted: origin_city=%s destination_city=%s route_city=%s interests=%s vibe=%s pace=%s trip_scope=%s",
        intent.origin_city,
        intent.destination_city,
        intent.route_city,
        intent.interests,
        intent.vibe,
        intent.pace,
        intent.trip_scope,
    )

    page_url, page_content = await wikivoyage.fetch_city_page(route_city)
    rag_snippets = rag.retrieve_snippets(page_content, payload.query, top_k=3)

    generated_search_phrases = (
        await llm.generate_search_phrases(intent, rag_snippets)
        if settings.llm_enabled
        else make_search_phrases_fallback(payload.query, route_city, intent.interests)
    )

    if not generated_search_phrases:
        generated_search_phrases = make_search_phrases_fallback(payload.query, route_city, intent.interests)

    logger.info("Generated phrases count=%s", len(generated_search_phrases))

    candidates = await places.search_many(
        generated_search_phrases,
        city=route_city,
        results_per_query=5,
    )
    logger.info("Places fetched count=%s for route_city=%s", len(candidates), route_city)

    if not candidates:
        raise HTTPException(
            status_code=400,
            detail=f"Не удалось найти подходящие места для города {route_city}. Попробуй уточнить запрос.",
        )

    sources = Sources(
        wikivoyage_page=page_url,
        rag_snippets=rag_snippets,
        generated_search_phrases=generated_search_phrases,
    )

    return await planner.build_plan(
        request=payload,
        city=route_city,
        candidates=candidates,
        sources=sources,
        intent=intent,
    )
