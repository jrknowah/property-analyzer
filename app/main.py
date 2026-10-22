"""FastAPI entry point.

Phase 1 endpoints take the full property context in the request body, so the
engine can be exercised before the data pipeline exists. Once parcels and tracts
are loaded, add GET /parcels/{id} and let /analyze accept a parcel_id instead.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from app.models import AnalyzeRequest, AnalyzeResponse, ProgramMatch
from app.programs.matcher import MatchContext, load_programs, match_programs
from app.scoring.engine import analyze
from app.scoring.weights import MODEL_VERSION, WEIGHTS

app = FastAPI(title="Property Investment Analyzer", version=MODEL_VERSION)
PROGRAMS = load_programs()


class ReferenceData(BaseModel):
    """Optional lookup tables until the database layer supplies them."""
    area_median_income: Optional[dict[int, int]] = None
    agency_limits: dict[str, dict] = {}


class AnalyzeBody(AnalyzeRequest):
    reference: ReferenceData = ReferenceData()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_version": MODEL_VERSION, "programs_loaded": len(PROGRAMS)}


@app.get("/weights")
def weights() -> dict:
    return {s.value: w for s, w in WEIGHTS.items()}


@app.get("/programs")
def list_programs() -> list[dict]:
    return [
        {"id": p.id, "name": p.name, "agency": p.agency, "category": p.category,
         "amount_max": p.amount_max, "start_date": p.start_date, "end_date": p.end_date,
         "source_url": p.source_url, "last_verified": p.last_verified}
        for p in PROGRAMS
    ]


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze_property(body: AnalyzeBody) -> AnalyzeResponse:
    return analyze(
        body,
        area_median_income=body.reference.area_median_income,
        agency_limits=body.reference.agency_limits,
        programs=PROGRAMS,
    )


@app.post("/programs/match", response_model=list[ProgramMatch])
def match(body: AnalyzeBody) -> list[ProgramMatch]:
    ctx = MatchContext(
        property=body.property, budget=body.budget, strategy=body.strategy,
        as_of=body.as_of or date.today(),
        area_median_income=body.reference.area_median_income,
        agency_limits=body.reference.agency_limits,
    )
    return match_programs(ctx, PROGRAMS)
