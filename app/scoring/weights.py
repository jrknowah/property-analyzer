"""Strategy weights. Starting values from the spec — tune after back-testing.

Bump MODEL_VERSION whenever weights or factor formulas change; every stored
analysis records the version that produced it.
"""
from app.models import Strategy

MODEL_VERSION = "2026.10-a"

FACTORS = (
    "financial",
    "location",
    "growth",
    "risk",
    "incentives",
    "livability",
    "zoning",
)

WEIGHTS: dict[Strategy, dict[str, float]] = {
    Strategy.rental: {
        "financial": 0.30, "location": 0.20, "growth": 0.15, "risk": 0.15,
        "incentives": 0.10, "livability": 0.05, "zoning": 0.05,
    },
    Strategy.commercial: {
        "financial": 0.30, "location": 0.20, "growth": 0.20, "risk": 0.10,
        "incentives": 0.10, "livability": 0.00, "zoning": 0.10,
    },
    Strategy.personal: {
        "financial": 0.10, "location": 0.15, "growth": 0.10, "risk": 0.20,
        "incentives": 0.05, "livability": 0.35, "zoning": 0.05,
    },
    Strategy.tax: {
        "financial": 0.20, "location": 0.10, "growth": 0.15, "risk": 0.10,
        "incentives": 0.35, "livability": 0.00, "zoning": 0.10,
    },
}

# Over-budget deals can never score above this.
OVER_BUDGET_SCORE_CAP = 40

GRADES = ((80, "A"), (65, "B"), (50, "C"), (35, "D"), (0, "F"))

for _s, _w in WEIGHTS.items():
    assert abs(sum(_w.values()) - 1.0) < 1e-9, f"weights for {_s} must sum to 1"
    assert set(_w) == set(FACTORS), f"weights for {_s} must cover every factor"
