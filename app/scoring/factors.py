"""Factor scores, each 0-100 with plain-English notes.

Deliberately excluded (fair housing): crime rates, school ratings and any
neighborhood demographic composition. Risk data shown to users stays factual
(flood zone, insurance) rather than evaluative of who lives somewhere.
"""
from __future__ import annotations

from app.models import AnalyzeRequest, DealMetrics, FactorScore, Strategy

NEUTRAL = 50
MS_MEDIAN_HH_INCOME_REF = 55_000   # placeholder reference; load from ACS state row in production


def _scale(value: float, lo: float, hi: float) -> float:
    """Map value linearly so lo -> 0 and hi -> 100 (works for inverted ranges too)."""
    if hi == lo:
        return NEUTRAL
    return max(0.0, min(100.0, (value - lo) / (hi - lo) * 100))


def _avg(parts: list[float]) -> int:
    return round(sum(parts) / len(parts)) if parts else NEUTRAL


# ---------------------------------------------------------------------------

def financial(req: AnalyzeRequest, deal: DealMetrics) -> tuple[int, list[str]]:
    notes: list[str] = []
    b = req.budget

    if req.strategy == Strategy.personal:
        # Affordability: housing cost vs. income (28% front-end ratio).
        if b.household_income:
            monthly_cost = (deal.annual_debt_service + deal.assumptions["annual_taxes"]
                            + deal.assumptions["annual_insurance"]) / 12
            ratio = monthly_cost / (b.household_income / 12) * 100
            notes.append(f"Housing cost is {ratio:.0f}% of gross monthly income (28% is the usual ceiling).")
            return round(_scale(ratio, 45, 20)), notes
        notes.append("Add household income for an affordability score; using price vs. budget instead.")
        return round(_scale(deal.all_in_cost / b.max_price, 1.2, 0.7)), notes

    if deal.gross_rent == 0:
        notes.append("No rent estimate available; financial return is unscored.")
        return NEUTRAL, notes

    parts = [_scale(deal.cap_rate_pct, 4, 10)]
    notes.append(f"Cap rate {deal.cap_rate_pct:.1f}%.")
    if deal.cash_on_cash_pct is not None:
        parts.append(_scale(deal.cash_on_cash_pct, 0, b.target_coc_pct * 1.5))
        notes.append(f"Cash-on-cash {deal.cash_on_cash_pct:.1f}% vs. your {b.target_coc_pct:.0f}% target.")
    if deal.dscr is not None:
        parts.append(_scale(deal.dscr, 0.9, 1.5))
        if deal.dscr < 1.2:
            notes.append(f"DSCR {deal.dscr:.2f} is below the 1.20 most DSCR lenders require.")
        else:
            notes.append(f"DSCR {deal.dscr:.2f}.")
    if deal.cash_flow_annual is not None and deal.cash_flow_annual < 0:
        notes.append(f"Negative cash flow of ${-deal.cash_flow_annual:,.0f}/yr.")
    return _avg(parts), notes


def location(req: AnalyzeRequest) -> tuple[int, list[str]]:
    t = req.property.tract
    parts: list[float] = []
    notes: list[str] = []
    if t.population_5yr_pct is not None:
        parts.append(_scale(t.population_5yr_pct, -5, 10))
        notes.append(f"Tract population {t.population_5yr_pct:+.1f}% over 5 years.")
    if t.vacancy_rate is not None:
        parts.append(_scale(t.vacancy_rate, 25, 5))
        notes.append(f"Housing vacancy {t.vacancy_rate:.0f}%.")
    if t.unemployment_rate is not None:
        parts.append(_scale(t.unemployment_rate, 12, 3))
    if t.median_hh_income is not None:
        parts.append(_scale(t.median_hh_income / MS_MEDIAN_HH_INCOME_REF, 0.5, 1.5))
    if not parts:
        notes.append("No ACS data loaded for this tract.")
    return _avg(parts), notes


def growth(req: AnalyzeRequest) -> tuple[int, list[str]]:
    score = 40.0
    notes: list[str] = []
    for e in req.property.events:
        if e.distance_mi > 3:
            continue
        near = e.distance_mi <= 1
        if e.impact == "positive":
            score += 12 if near else 7
            notes.append(f"{e.title} ({e.distance_mi:.1f} mi).")
        elif e.impact == "negative":
            score -= 12 if near else 6
            notes.append(f"Watch: {e.title} ({e.distance_mi:.1f} mi).")
    if not notes:
        notes.append("No tracked permits, rezonings or new businesses within 3 miles.")
    return round(max(0, min(100, score))), notes


def risk(req: AnalyzeRequest, deal: DealMetrics) -> tuple[int, list[str]]:
    """Higher = safer."""
    p = req.property
    zone = (p.fema_zone or "").upper()
    notes: list[str] = []
    if zone in ("VE", "V"):
        score = 10.0
        notes.append(f"FEMA Zone {zone}: coastal high-hazard area, flood insurance required with a mortgage.")
    elif zone in ("AE", "A", "AO", "AH"):
        score = 35.0
        notes.append(f"FEMA Zone {zone}: Special Flood Hazard Area, flood insurance required with a mortgage.")
    elif zone in ("X500", "B"):
        score = 70.0
        notes.append("Moderate flood risk (0.2% annual chance zone).")
    elif zone in ("X", "C"):
        score = 90.0
        notes.append("FEMA Zone X: minimal flood risk.")
    else:
        score = 60.0
        notes.append("Flood zone unknown.")

    ins_share = deal.assumptions["annual_insurance"] / p.price * 100
    if ins_share > 1.5:
        score -= 15
        notes.append(f"Insurance estimate is {ins_share:.1f}% of price per year; get a real quote.")
    return round(max(0, min(100, score))), notes


def incentives(req: AnalyzeRequest) -> tuple[int, list[str]]:
    t = req.property.tract
    score = 20.0
    notes: list[str] = []
    if t.oz_2_0:
        score += 40
        if t.rural_flag:
            score += 20
            notes.append("Rural Opportunity Zone (2027-2036): eligible for the 30% basis step-up via a QROF.")
        else:
            notes.append("Opportunity Zone 2.0 tract (2027-2036): 10% basis step-up after 5 years.")
    elif t.oz_1_0:
        score += 15
        notes.append("Legacy OZ 1.0 tract: designation runs through 2028; new investments need careful timing.")
    if t.qct or t.dda:
        score += 15
        notes.append("HUD QCT/DDA: up to 30% LIHTC basis boost for affordable housing.")
    if t.usda_eligible:
        score += 10 if req.strategy == Strategy.personal else 5
        notes.append("USDA rural-eligible area.")
    if req.strategy == Strategy.tax and (t.oz_2_0 or t.oz_1_0) and req.budget.has_capital_gains is False:
        notes.append("OZ benefits require reinvested capital gains; you indicated none.")
    if not notes:
        notes.append("No place-based incentives found for this tract.")
    return round(min(100, score)), notes


def livability(req: AnalyzeRequest) -> tuple[int, list[str]]:
    return NEUTRAL, ["Commute and amenity data arrive in Phase 2; scored neutral for now."]


ZONING_FIT = {
    # strategy -> zoning_type -> score
    Strategy.rental: {"residential": 90, "mixed": 85, "commercial": 45, "agricultural": 50, "industrial": 10},
    Strategy.commercial: {"commercial": 95, "mixed": 85, "industrial": 75, "residential": 25, "agricultural": 40},
    Strategy.personal: {"residential": 95, "mixed": 75, "agricultural": 80, "commercial": 30, "industrial": 5},
    Strategy.tax: {"commercial": 85, "mixed": 85, "residential": 80, "industrial": 75, "agricultural": 60},
}


def zoning(req: AnalyzeRequest) -> tuple[int, list[str]]:
    z = (req.property.zoning_type or "").lower()
    if not z:
        return NEUTRAL, ["Zoning unknown; confirm with the county or city before offering."]
    score = ZONING_FIT[req.strategy].get(z, NEUTRAL)
    notes = [f"Zoned {z}."]
    if score < 50:
        notes.append(f"{z.capitalize()} zoning is a poor fit for a {req.strategy.value} strategy; a rezoning would be needed.")
    if req.property.units > 1 and req.strategy == Strategy.rental and z == "residential":
        notes.append("Confirm the residential district allows multifamily at this unit count.")
    for e in req.property.events:
        if e.event_type == "rezoning" and e.distance_mi <= 0.5:
            notes.append(f"Nearby rezoning activity: {e.title}.")
    return score, notes


def all_factors(req: AnalyzeRequest, deal: DealMetrics, weights: dict[str, float]) -> list[FactorScore]:
    results = {
        "financial": financial(req, deal),
        "location": location(req),
        "growth": growth(req),
        "risk": risk(req, deal),
        "incentives": incentives(req),
        "livability": livability(req),
        "zoning": zoning(req),
    }
    return [FactorScore(factor=name, score=s, weight=weights[name], notes=n)
            for name, (s, n) in results.items()]
