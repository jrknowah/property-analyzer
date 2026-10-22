"""Scoring engine: budget gate -> deal math -> factor scores -> weighted total.

Scores are deterministic and versioned. An LLM may later turn `factors[].notes`
into prose, but never produces or adjusts a score.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.models import AnalyzeRequest, AnalyzeResponse
from app.programs.matcher import (MatchContext, Program, load_programs,
                                  match_programs, potential_assistance)
from app.scoring import factors as F
from app.scoring.deal_math import compute_deal
from app.scoring.weights import GRADES, MODEL_VERSION, OVER_BUDGET_SCORE_CAP, WEIGHTS

DISCLAIMER = (
    "Estimates for research only; not financial, tax, legal or lending advice. "
    "Verify program terms with the agency and consult a tax professional before relying on "
    "Opportunity Zone or other tax benefits."
)


def grade(score: int) -> str:
    return next(g for floor, g in GRADES if score >= floor)


def analyze(
    req: AnalyzeRequest,
    *,
    area_median_income: Optional[dict[int, int]] = None,
    agency_limits: Optional[dict[str, dict]] = None,
    programs: Optional[list[Program]] = None,
) -> AnalyzeResponse:
    as_of = req.as_of or date.today()
    programs = programs if programs is not None else load_programs()

    deal = compute_deal(req)
    weights = WEIGHTS[req.strategy]
    factor_scores = F.all_factors(req, deal, weights)
    total = round(sum(f.score * f.weight for f in factor_scores))

    ctx = MatchContext(
        property=req.property, budget=req.budget, strategy=req.strategy, as_of=as_of,
        area_median_income=area_median_income, agency_limits=agency_limits or {},
    )
    matches = match_programs(ctx, programs)
    assistance = potential_assistance(matches, programs)
    cash_gap = round(max(0.0, deal.cash_required - assistance - req.budget.cash_available), 2)

    flags: list[str] = []
    if deal.all_in_cost > req.budget.max_price:
        over = deal.all_in_cost - req.budget.max_price
        flags.append(f"Over budget by ${over:,.0f} (all-in ${deal.all_in_cost:,.0f} vs. max ${req.budget.max_price:,.0f}).")
        total = min(total, OVER_BUDGET_SCORE_CAP)
    if cash_gap > 0:
        flags.append(f"Short ${cash_gap:,.0f} in cash after potential assistance.")
    if any(m.needs_reverification for m in matches if m.status != "not_eligible"):
        flags.append("Some program data is over 90 days old; verify with the agency.")

    return AnalyzeResponse(
        total_score=total,
        grade=grade(total),
        strategy=req.strategy,
        flags=flags,
        factors=factor_scores,
        deal=deal,
        programs=matches,
        assistance_total=assistance,
        cash_gap_after_assistance=cash_gap,
        model_version=MODEL_VERSION,
        disclaimer=DISCLAIMER,
    )
