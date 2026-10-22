"""FastAPI entry point.

/analyze takes the full property context in the request body (no database needed).
/analyze/address geocodes an address and pulls tract, HUD and FEMA data from the
loaded database. Parcel-level data (owner, assessed value, zoning) comes next.
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


# ---------------------------------------------------------------------------
# Address-based analysis (uses loaded tract, HUD and FEMA data)
# ---------------------------------------------------------------------------

from fastapi import HTTPException  # noqa: E402

from app.data import config as data_config  # noqa: E402
from app.data.clients import DataSourceError  # noqa: E402
from app.data.enrich import lookup_address  # noqa: E402
from app.data.loaders import connect  # noqa: E402
from app.models import BudgetProfile, Property, RentAssumptions, Strategy  # noqa: E402


class AddressAnalyzeBody(BaseModel):
    address: str
    price: float
    strategy: Strategy
    budget: BudgetProfile
    property_type: str = "single_family"
    units: int = 1
    bedrooms: Optional[int] = None
    zoning_type: Optional[str] = None
    annual_tax: Optional[float] = None
    est_rehab: float = 0
    rent: RentAssumptions = RentAssumptions()
    agency_limits: dict[str, dict] = {}
    as_of: Optional[date] = None


class AddressAnalyzeResponse(BaseModel):
    matched_address: str
    lon: float
    lat: float
    data_warnings: list[str]
    analysis: AnalyzeResponse


@app.post("/analyze/address", response_model=AddressAnalyzeResponse)
def analyze_address(body: AddressAnalyzeBody) -> AddressAnalyzeResponse:
    try:
        with connect(data_config.DATABASE_URL) as conn:
            ctx = lookup_address(conn, body.address)
    except DataSourceError as e:
        raise HTTPException(502, f"Lookup service unavailable: {e}")
    if ctx is None:
        raise HTTPException(404, "Address not found by the Census geocoder.")

    rent = body.rent.model_copy()
    if rent.fmr_per_unit is None:
        fmr = ctx.fmr_for(body.bedrooms)
        rent.fmr_per_unit = float(fmr) if fmr else None

    req = AnalyzeRequest(
        strategy=body.strategy, budget=body.budget, rent=rent, as_of=body.as_of,
        property=Property(
            parcel_id=f"addr:{ctx.lon:.6f},{ctx.lat:.6f}", address=ctx.matched_address,
            county_fips=ctx.county_fips, price=body.price, property_type=body.property_type,
            units=body.units, bedrooms=body.bedrooms, zoning_type=body.zoning_type,
            fema_zone=ctx.fema_zone, annual_tax=body.annual_tax, est_rehab=body.est_rehab,
            tract=ctx.tract,
        ),
    )
    result = analyze(req, area_median_income=ctx.area_median_income,
                     agency_limits=body.agency_limits, programs=PROGRAMS)
    return AddressAnalyzeResponse(matched_address=ctx.matched_address, lon=ctx.lon, lat=ctx.lat,
                                  data_warnings=ctx.warnings, analysis=result)
