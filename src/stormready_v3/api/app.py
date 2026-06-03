from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from stormready_v3.agents.factory import build_agent_dispatcher
from stormready_v3.ai.factory import build_agent_model_provider
from stormready_v3.config.settings import background_supervisor_enabled, background_supervisor_interval_seconds
from stormready_v3.storage.db import Database

from .service import (
    bootstrap_state,
    build_workspace,
    complete_onboarding,
    delete_operator_profile,
    get_chat_history,
    review_historical_upload,
    submit_service_plan,
    submit_actual_entry,
    request_refresh_now,
    start_setup_bootstrap_now,
    post_chat_message,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
LOGGER = logging.getLogger(__name__)


def _background_supervisor_enabled() -> bool:
    return background_supervisor_enabled()


def _background_supervisor_interval_seconds() -> int:
    return background_supervisor_interval_seconds()


class _BackgroundSupervisorLoop:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if not _background_supervisor_enabled():
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="stormready-background-supervisor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def _run(self) -> None:
        interval_seconds = _background_supervisor_interval_seconds()
        while not self._stop_event.is_set():
            db = Database()
            try:
                from stormready_v3.orchestration.orchestrator import DeterministicOrchestrator
                from stormready_v3.orchestration.supervisor import SupervisorService

                db.initialize()
                provider = build_agent_model_provider()
                dispatcher = build_agent_dispatcher(db, provider)
                orchestrator = DeterministicOrchestrator(db, agent_dispatcher=dispatcher)
                orchestrator.initialize()
                supervisor = SupervisorService(orchestrator)
                result = supervisor.run_tick()
                LOGGER.info(
                    "background supervisor tick completed operators=%s queue=%s scheduled=%s event=%s",
                    len(result.processed_operator_ids),
                    result.queued_requests_completed,
                    result.scheduled_runs,
                    result.event_mode_runs,
                )
            except Exception:
                LOGGER.exception("background supervisor tick failed")
            finally:
                db.close()
            if self._stop_event.wait(interval_seconds):
                break


_BACKGROUND_SUPERVISOR_LOOP = _BackgroundSupervisorLoop()


@asynccontextmanager
async def _lifespan(app_instance: FastAPI):
    provider = build_agent_model_provider()
    agent_db = Database()
    agent_db.initialize()
    app_instance.state.agent_dispatcher = build_agent_dispatcher(agent_db, provider)
    _BACKGROUND_SUPERVISOR_LOOP.start()
    try:
        yield
    finally:
        _BACKGROUND_SUPERVISOR_LOOP.stop()
        agent_db.close()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    referenceDate: str | None = None
    learningAgendaKey: str | None = None


class RefreshRequest(BaseModel):
    reason: str | None = None
    referenceDate: str | None = None


class ActualEntryRequest(BaseModel):
    serviceDate: str
    realizedTotalCovers: int
    realizedReservedCovers: int | None = None
    realizedWalkInCovers: int | None = None
    outsideCovers: int | None = None
    serviceState: str = "normal_service"
    note: str = ""
    referenceDate: str | None = None


class ServicePlanRequest(BaseModel):
    serviceDate: str
    serviceState: str = "normal_service"
    plannedTotalCovers: int | None = None
    estimatedReductionPct: float | None = None
    note: str = ""
    reviewWindowStart: str | None = None
    reviewWindowEnd: str | None = None
    referenceDate: str | None = None


class OnboardingRequest(BaseModel):
    operatorId: str | None = None
    restaurantName: str
    canonicalAddress: str
    city: str = ""
    timezone: str = "America/New_York"
    forecastInputMode: str = "manual_baselines"
    historicalUploadToken: str | None = None
    historicalUploadReview: dict[str, Any] | None = None
    monThu: int = 0
    fri: int = 0
    sat: int = 0
    sun: int = 0
    demandMix: str = "mixed"
    neighborhoodType: str = "mixed_urban"
    patioEnabled: bool = False
    patioSeatCapacity: int = 0
    patioSeasonMode: str = "seasonal"
    transitRelevance: bool = False
    venueRelevance: bool = False
    hotelTravelRelevance: bool = False


class HistoricalUploadReviewRequest(BaseModel):
    fileName: str = Field(min_length=1)
    content: str = Field(min_length=1)


def _db_dependency() -> Database:
    db = Database()
    db.initialize()
    try:
        yield db
    finally:
        db.close()


app = FastAPI(title="StormReady API", version="0.1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/api/bootstrap")
def bootstrap(db: Database = Depends(_db_dependency)) -> dict[str, Any]:
    return bootstrap_state(db)


@app.get("/api/operators/{operator_id}/workspace")
def operator_workspace(
    operator_id: str,
    referenceDate: str | None = Query(default=None),
    db: Database = Depends(_db_dependency),
) -> dict[str, Any]:
    return build_workspace(db, operator_id, reference_date=referenceDate)


@app.post("/api/onboarding/complete")
def finish_onboarding(payload: OnboardingRequest, db: Database = Depends(_db_dependency)) -> dict[str, Any]:
    try:
        return complete_onboarding(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/onboarding/review-history-upload")
def review_history_upload(
    payload: HistoricalUploadReviewRequest,
    db: Database = Depends(_db_dependency),
) -> dict[str, Any]:
    try:
        return review_historical_upload(
            db,
            file_name=payload.fileName,
            content=payload.content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/operators/{operator_id}/chat")
def chat(
    operator_id: str,
    payload: ChatRequest,
    request: Request,
    db: Database = Depends(_db_dependency),
) -> dict[str, Any]:
    return post_chat_message(
        db,
        operator_id,
        payload.message,
        reference_date=payload.referenceDate,
        learning_agenda_key=payload.learningAgendaKey,
        agent_dispatcher=getattr(request.app.state, "agent_dispatcher", None),
    )


@app.get("/api/operators/{operator_id}/chat-history")
def chat_history(
    operator_id: str,
    beforeId: int | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    db: Database = Depends(_db_dependency),
) -> dict[str, Any]:
    return get_chat_history(db, operator_id, before_id=beforeId, limit=limit)


@app.post("/api/operators/{operator_id}/actuals")
def submit_actuals(
    operator_id: str,
    payload: ActualEntryRequest,
    request: Request,
    db: Database = Depends(_db_dependency),
) -> dict[str, Any]:
    try:
        payload_dict = payload.model_dump()
        reference_date = payload_dict.pop("referenceDate", None)
        return submit_actual_entry(
            db,
            operator_id,
            payload_dict,
            reference_date=reference_date,
            agent_dispatcher=getattr(request.app.state, "agent_dispatcher", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/operators/{operator_id}/service-plan")
def save_service_plan(
    operator_id: str,
    payload: ServicePlanRequest,
    request: Request,
    db: Database = Depends(_db_dependency),
) -> dict[str, Any]:
    try:
        payload_dict = payload.model_dump()
        reference_date = payload_dict.pop("referenceDate", None)
        return submit_service_plan(
            db,
            operator_id,
            payload_dict,
            reference_date=reference_date,
            agent_dispatcher=getattr(request.app.state, "agent_dispatcher", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/operators/{operator_id}/refresh")
def refresh(
    operator_id: str,
    payload: RefreshRequest,
    request: Request,
    db: Database = Depends(_db_dependency),
) -> dict[str, Any]:
    return request_refresh_now(
        db,
        operator_id,
        payload.reason,
        reference_date=payload.referenceDate,
        agent_dispatcher=getattr(request.app.state, "agent_dispatcher", None),
    )


@app.post("/api/operators/{operator_id}/setup-bootstrap")
def start_setup_bootstrap(
    operator_id: str,
    referenceDate: str | None = Query(default=None),
    db: Database = Depends(_db_dependency),
) -> dict[str, Any]:
    return start_setup_bootstrap_now(db, operator_id, reference_date=referenceDate)


@app.delete("/api/operators/{operator_id}")
def delete_operator(operator_id: str, db: Database = Depends(_db_dependency)) -> dict[str, Any]:
    delete_operator_profile(db, operator_id)
    return {"deleted": True, "operatorId": operator_id}


@app.get("/{full_path:path}")
def spa(full_path: str) -> FileResponse:
    if full_path.startswith("api"):
        raise HTTPException(status_code=404, detail="Not found")
    index_file = FRONTEND_DIST / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend build not found")
    return FileResponse(index_file)
