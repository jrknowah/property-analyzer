from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Strategy
from app.programs.matcher import MatchContext, load_programs, match_programs
from app.scoring.deal_math import compute_deal, monthly_payment
from app.scoring.engine import analyze
from app.scoring.weights import OVER_BUDGET_SCORE_CAP, WEIGHTS
from tests.fixtures import (aberdeen_rural_oz_land, golden_triangle_first_time_buyer,
                            gulf_coast_rental)

PROGRAMS = load_programs()


def by_id(resp, pid):
    return next(m for m in resp.programs if m.program_id == pid)


# --- deal math --------------------------------------------------------------

def test_monthly_payment_matches_standard_amortization():
    # $100k, 6%, 30 years -> $599.55
    assert monthly_payment(100_000, 6.0, 30) == pytest.approx(599.55, abs=0.01)


def test_rental_deal_math():
    deal = compute_deal(gulf_coast_rental())
    assert deal.down_payment == 37_000            # 20% of 185k
    assert deal.closing_costs == 5_550            # 3%
    assert deal.cash_required == 42_550
    assert deal.gross_rent == 27_600              # 1,150 x 2 units x 12
    assert deal.assumptions["rent_source"] == "hud_fmr"
    # Zone AE x coastal wind: 185k x 0.6% x 2.0 x 1.6
    assert deal.assumptions["annual_insurance"] == pytest.approx(3_552.0)
    assert deal.dscr is not None and deal.cash_on_cash_pct is not None


# --- scoring ----------------------------------------------------------------

def test_weights_sum_to_one():
    for w in WEIGHTS.values():
        assert sum(w.values()) == pytest.approx(1.0)


def test_scores_are_bounded_and_deterministic():
    for make in (gulf_coast_rental, aberdeen_rural_oz_land, golden_triangle_first_time_buyer):
        a, b = analyze(make(), programs=PROGRAMS), analyze(make(), programs=PROGRAMS)
        assert 0 <= a.total_score <= 100
        assert a.total_score == b.total_score
        assert all(0 <= f.score <= 100 for f in a.factors)


def test_over_budget_caps_score():
    req = gulf_coast_rental()
    req.budget.max_price = 150_000
    resp = analyze(req, programs=PROGRAMS)
    assert resp.total_score <= OVER_BUDGET_SCORE_CAP
    assert any("Over budget" in f for f in resp.flags)


def test_flood_zone_lowers_risk_score():
    req = gulf_coast_rental()
    ae = next(f for f in analyze(req, programs=PROGRAMS).factors if f.factor == "risk").score
    req.property.fema_zone = "X"
    x = next(f for f in analyze(req, programs=PROGRAMS).factors if f.factor == "risk").score
    assert x > ae


def test_rural_oz_drives_tax_strategy_incentives():
    resp = analyze(aberdeen_rural_oz_land(), programs=PROGRAMS)
    inc = next(f for f in resp.factors if f.factor == "incentives")
    assert inc.score >= 80
    assert by_id(resp, "oz-2-0-rural").status == "eligible"


# --- program matcher ----------------------------------------------------------

def test_oz_2_0_is_upcoming_before_2027():
    req = aberdeen_rural_oz_land()
    req.as_of = date(2026, 10, 22)
    resp = analyze(req, programs=PROGRAMS)
    assert by_id(resp, "oz-2-0-rural").status == "upcoming"


def test_expired_program_is_not_eligible():
    resp = analyze(golden_triangle_first_time_buyer(), programs=PROGRAMS)
    dpa14 = by_id(resp, "mhc-dpa14")
    assert dpa14.status == "not_eligible"
    assert any("ended 2026-04-30" in r for r in dpa14.failed_rules)


def test_first_time_buyer_matches_without_agency_tables():
    resp = analyze(golden_triangle_first_time_buyer(), programs=PROGRAMS)
    assert by_id(resp, "mhc-easy8").status == "likely"      # MHC tables not loaded
    assert by_id(resp, "mhc-hat").status == "eligible"      # teacher
    assert by_id(resp, "fha-203b").status == "eligible"
    assert by_id(resp, "va-home-loan").status == "not_eligible"
    assert by_id(resp, "oz-2-0").status == "not_eligible"  # personal residence


def test_agency_tables_resolve_likely_to_eligible():
    resp = analyze(golden_triangle_first_time_buyer(), programs=PROGRAMS,
                   agency_limits={"mhc": {"income_max": {3: 90_000}, "price_max": 250_000}},
                   area_median_income={3: 70_000})
    assert by_id(resp, "mhc-easy8").status == "eligible"
    # 52k <= 80% of 70k (56k) -> direct loan eligible
    assert by_id(resp, "usda-502-direct").status == "eligible"


def test_dpa_stack_group_counts_only_largest():
    resp = analyze(golden_triangle_first_time_buyer(), programs=PROGRAMS)
    # Home4All 25k + largest MHC DPA (Easy8 8k) + HAT 6k; Smart6/MRB7 don't stack with Easy8
    assert resp.assistance_total == 25_000 + 8_000 + 6_000


def test_investor_gets_no_homebuyer_programs():
    resp = analyze(gulf_coast_rental(), programs=PROGRAMS)
    homebuyer = [m for m in resp.programs if m.category == "homebuyer"]
    assert all(m.status == "not_eligible" for m in homebuyer)


def test_matches_sorted_eligible_first():
    ctx = MatchContext(property=golden_triangle_first_time_buyer().property,
                       budget=golden_triangle_first_time_buyer().budget,
                       strategy=Strategy.personal, as_of=date(2026, 10, 22))
    order = [m.status for m in match_programs(ctx, PROGRAMS)]
    rank = {"eligible": 0, "likely": 1, "upcoming": 2, "not_eligible": 3}
    assert order == sorted(order, key=rank.get)


# --- API --------------------------------------------------------------------

def test_api_analyze_and_health():
    client = TestClient(app)
    assert client.get("/health").json()["programs_loaded"] == len(PROGRAMS)
    body = gulf_coast_rental().model_dump(mode="json")
    r = client.post("/analyze", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["grade"] in "ABCDF" and data["disclaimer"]
    assert len(data["factors"]) == 7
