"""Request/response models shared by the scoring engine, program matcher and API."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Strategy(str, Enum):
    rental = "rental"
    commercial = "commercial"
    personal = "personal"
    tax = "tax"


class Financing(str, Enum):
    cash = "cash"
    conventional = "conventional"
    fha = "fha"
    va = "va"
    usda = "usda"
    dscr = "dscr"


class Tract(BaseModel):
    """Census-tract context. In production this is loaded from the `tracts` table."""
    tract_geoid: str
    county_fips: str
    median_hh_income: Optional[int] = None
    population_5yr_pct: Optional[float] = None   # % change over 5 years
    vacancy_rate: Optional[float] = None          # %
    unemployment_rate: Optional[float] = None     # %
    oz_1_0: bool = False
    oz_2_0: bool = False
    rural_flag: bool = False
    usda_eligible: Optional[bool] = None
    qct: bool = False
    dda: bool = False


class AreaEvent(BaseModel):
    event_type: str                  # permit / rezoning / new_business / ed_project / infrastructure
    title: str
    distance_mi: float
    impact: str = "neutral"          # positive / neutral / negative
    event_date: Optional[date] = None


class Property(BaseModel):
    parcel_id: str
    address: str
    city: Optional[str] = None
    county_fips: str
    price: float = Field(..., gt=0, description="Asking or offer price")
    property_type: str = "single_family"   # single_family / multifamily / commercial / land
    units: int = 1
    bedrooms: Optional[int] = None         # per unit, used for FMR lookup
    zoning_type: Optional[str] = None      # residential / commercial / mixed / agricultural / industrial
    fema_zone: Optional[str] = None
    annual_tax: Optional[float] = None
    est_rehab: float = 0
    tract: Tract
    events: list[AreaEvent] = []


class BudgetProfile(BaseModel):
    max_price: float = Field(..., gt=0, description="Max all-in cost (price + closing + rehab)")
    cash_available: float = Field(..., ge=0)
    target_coc_pct: float = 8.0
    financing: Financing = Financing.conventional
    interest_rate_pct: float = 7.0
    loan_term_years: int = 30
    household_size: Optional[int] = None
    household_income: Optional[int] = None
    first_time_buyer: Optional[bool] = None
    veteran: Optional[bool] = None
    occupation: Optional[str] = None
    has_capital_gains: Optional[bool] = None


class RentAssumptions(BaseModel):
    """Overrides for the deal calculator. Anything left None falls back to a default."""
    monthly_rent_per_unit: Optional[float] = None   # default: HUD FMR for the bedroom count
    fmr_per_unit: Optional[float] = None            # HUD FMR, supplied by the data layer
    vacancy_pct: float = 8.0
    management_pct: float = 9.0
    maintenance_pct: float = 6.0
    annual_insurance: Optional[float] = None        # default: flood-zone-adjusted estimate
    other_annual_opex: float = 0.0


class AnalyzeRequest(BaseModel):
    property: Property
    budget: BudgetProfile
    strategy: Strategy
    rent: RentAssumptions = RentAssumptions()
    as_of: Optional[date] = None   # program eligibility date; defaults to today


class FactorScore(BaseModel):
    factor: str
    score: int
    weight: float
    notes: list[str] = []


class DealMetrics(BaseModel):
    all_in_cost: float
    down_payment: float
    closing_costs: float
    cash_required: float
    loan_amount: float
    annual_debt_service: float
    gross_rent: float
    noi: Optional[float]
    cap_rate_pct: Optional[float]
    cash_flow_annual: Optional[float]
    cash_on_cash_pct: Optional[float]
    dscr: Optional[float]
    rent_vs_fmr_pct: Optional[float]
    assumptions: dict


class ProgramMatch(BaseModel):
    program_id: str
    name: str
    agency: str
    category: str
    status: str                    # eligible / likely / not_eligible
    benefit_text: str
    amount_max: Optional[int] = None
    failed_rules: list[str] = []
    missing_inputs: list[str] = []
    source_url: str
    last_verified: date
    needs_reverification: bool = False


class AnalyzeResponse(BaseModel):
    total_score: int
    grade: str
    strategy: Strategy
    flags: list[str]
    factors: list[FactorScore]
    deal: DealMetrics
    programs: list[ProgramMatch]
    assistance_total: int
    cash_gap_after_assistance: float
    model_version: str
    disclaimer: str
