"""Program eligibility rules engine.

Programs and rules are data (seed_programs.json in dev, the `programs` and
`program_rules` tables in production). Each rule evaluates to PASS, FAIL or
UNKNOWN (an input is missing). A program is:

  not_eligible  any rule fails, or the program has ended
  upcoming      no rule fails, but the program has not started yet
  likely        no rule fails, but at least one input is missing
  eligible      every rule passes
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from app.models import BudgetProfile, ProgramMatch, Property, Strategy

SEED_PATH = Path(__file__).with_name("seed_programs.json")
REVERIFY_AFTER = timedelta(days=90)
STATUS_ORDER = {"eligible": 0, "likely": 1, "upcoming": 2, "not_eligible": 3}


class R(Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass
class MatchContext:
    property: Property
    budget: BudgetProfile
    strategy: Strategy
    as_of: date
    # 100% area median income by household size, e.g. {1: 52000, 2: 59400, ...}
    area_median_income: Optional[dict[int, int]] = None
    # Agency-specific county tables, e.g. {"mhc": {"income_max": {3: 98000}, "price_max": 332000}}
    agency_limits: dict[str, dict] = field(default_factory=dict)


@dataclass
class Program:
    id: str
    name: str
    agency: str
    category: str
    benefit_text: str
    source_url: str
    last_verified: date
    rules: list[dict]
    amount_max: Optional[int] = None
    stack_group: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


# ---------------------------------------------------------------------------
# Rule evaluators: (ctx, params) -> (result, missing_input or None)
# ---------------------------------------------------------------------------

Result = tuple[R, Optional[str]]


def _bool(value: Optional[bool], missing: str) -> Result:
    if value is None:
        return R.UNKNOWN, missing
    return (R.PASS if value else R.FAIL), None


def r_state(ctx: MatchContext, p: dict) -> Result:
    return (R.PASS if ctx.property.county_fips.startswith(p["state_fips"]) else R.FAIL), None


def r_counties(ctx: MatchContext, p: dict) -> Result:
    return (R.PASS if ctx.property.county_fips in p["county_fips"] else R.FAIL), None


def r_owner_occupied(ctx: MatchContext, p: dict) -> Result:
    return (R.PASS if ctx.strategy == Strategy.personal else R.FAIL), None


def r_first_time_buyer(ctx: MatchContext, p: dict) -> Result:
    return _bool(ctx.budget.first_time_buyer, "first-time buyer status")


def r_veteran(ctx: MatchContext, p: dict) -> Result:
    return _bool(ctx.budget.veteran, "veteran status")


def r_capital_gains(ctx: MatchContext, p: dict) -> Result:
    return _bool(ctx.budget.has_capital_gains, "whether you have capital gains to reinvest")


def r_occupation(ctx: MatchContext, p: dict) -> Result:
    occ = (ctx.budget.occupation or "").strip().lower()
    if not occ:
        return R.UNKNOWN, "occupation"
    return (R.PASS if any(k in occ for k in p["any_of"]) else R.FAIL), None


def r_tract_flag(ctx: MatchContext, p: dict) -> Result:
    value = getattr(ctx.property.tract, p["flag"])
    return _bool(value, f"tract data: {p['flag']}")


def r_any_tract_flag(ctx: MatchContext, p: dict) -> Result:
    return (R.PASS if any(getattr(ctx.property.tract, f) for f in p["flags"]) else R.FAIL), None


def r_price_max(ctx: MatchContext, p: dict) -> Result:
    return (R.PASS if ctx.property.price <= p["max"] else R.FAIL), None


def r_units_max(ctx: MatchContext, p: dict) -> Result:
    return (R.PASS if ctx.property.units <= p["max"] else R.FAIL), None


def r_property_type_in(ctx: MatchContext, p: dict) -> Result:
    return (R.PASS if ctx.property.property_type in p["types"] else R.FAIL), None


def r_strategy_in(ctx: MatchContext, p: dict) -> Result:
    return (R.PASS if ctx.strategy.value in p["strategies"] else R.FAIL), None


def r_rehab_needed(ctx: MatchContext, p: dict) -> Result:
    return (R.PASS if ctx.property.est_rehab > 0 else R.FAIL), None


def r_income_max_pct_ami(ctx: MatchContext, p: dict) -> Result:
    b = ctx.budget
    if b.household_income is None or b.household_size is None:
        return R.UNKNOWN, "household income and size"
    if not ctx.area_median_income:
        return R.UNKNOWN, "HUD income limits for this county"
    size = min(b.household_size, 8)
    ami = ctx.area_median_income.get(size)
    if ami is None:
        return R.UNKNOWN, "HUD income limits for this county"
    return (R.PASS if b.household_income <= ami * p["pct"] / 100 else R.FAIL), None


def r_agency_income_limit(ctx: MatchContext, p: dict) -> Result:
    b = ctx.budget
    if b.household_income is None or b.household_size is None:
        return R.UNKNOWN, "household income and size"
    table = ctx.agency_limits.get(p["agency"], {}).get("income_max")
    if not table:
        return R.UNKNOWN, f"{p['agency'].upper()} income limits for this county"
    limit = table.get(min(b.household_size, max(table)))
    return (R.PASS if limit is not None and b.household_income <= limit else R.FAIL), None


def r_agency_price_limit(ctx: MatchContext, p: dict) -> Result:
    limit = ctx.agency_limits.get(p["agency"], {}).get("price_max")
    if limit is None:
        return R.UNKNOWN, f"{p['agency'].upper()} purchase price limit for this county"
    return (R.PASS if ctx.property.price <= limit else R.FAIL), None


RULE_TYPES: dict[str, Callable[[MatchContext, dict], Result]] = {
    "state": r_state,
    "counties": r_counties,
    "owner_occupied": r_owner_occupied,
    "first_time_buyer": r_first_time_buyer,
    "veteran": r_veteran,
    "capital_gains": r_capital_gains,
    "occupation": r_occupation,
    "tract_flag": r_tract_flag,
    "any_tract_flag": r_any_tract_flag,
    "price_max": r_price_max,
    "units_max": r_units_max,
    "property_type_in": r_property_type_in,
    "strategy_in": r_strategy_in,
    "rehab_needed": r_rehab_needed,
    "income_max_pct_ami": r_income_max_pct_ami,
    "agency_income_limit": r_agency_income_limit,
    "agency_price_limit": r_agency_price_limit,
}


# ---------------------------------------------------------------------------

def _date(v: Optional[str]) -> Optional[date]:
    return date.fromisoformat(v) if v else None


def load_programs(path: Path = SEED_PATH) -> list[Program]:
    raw = json.loads(path.read_text())["programs"]
    programs = []
    for row in raw:
        for rule in row["rules"]:
            if rule["rule_type"] not in RULE_TYPES:
                raise ValueError(f"{row['id']}: unknown rule_type {rule['rule_type']!r}")
        programs.append(Program(
            id=row["id"], name=row["name"], agency=row["agency"], category=row["category"],
            benefit_text=row["benefit_text"], source_url=row["source_url"],
            last_verified=date.fromisoformat(row["last_verified"]), rules=row["rules"],
            amount_max=row.get("amount_max"), stack_group=row.get("stack_group"),
            start_date=_date(row.get("start_date")), end_date=_date(row.get("end_date")),
        ))
    return programs


def evaluate(program: Program, ctx: MatchContext) -> ProgramMatch:
    failed: list[str] = []
    missing: list[str] = []
    for rule in program.rules:
        result, missing_input = RULE_TYPES[rule["rule_type"]](ctx, rule.get("params", {}))
        if result == R.FAIL:
            failed.append(rule["description"])
        elif result == R.UNKNOWN and missing_input and missing_input not in missing:
            missing.append(missing_input)

    if program.end_date and ctx.as_of > program.end_date:
        failed.insert(0, f"Program ended {program.end_date.isoformat()}.")

    if failed:
        status = "not_eligible"
    elif program.start_date and ctx.as_of < program.start_date:
        status = "upcoming"
    elif missing:
        status = "likely"
    else:
        status = "eligible"

    return ProgramMatch(
        program_id=program.id, name=program.name, agency=program.agency,
        category=program.category, status=status, benefit_text=program.benefit_text,
        amount_max=program.amount_max, failed_rules=failed, missing_inputs=missing,
        source_url=program.source_url, last_verified=program.last_verified,
        needs_reverification=(ctx.as_of - program.last_verified) > REVERIFY_AFTER,
    )


def match_programs(ctx: MatchContext, programs: Optional[list[Program]] = None) -> list[ProgramMatch]:
    programs = programs if programs is not None else load_programs()
    matches = [evaluate(p, ctx) for p in programs]
    return sorted(matches, key=lambda m: (STATUS_ORDER[m.status], -(m.amount_max or 0), m.name))


def potential_assistance(matches: list[ProgramMatch], programs: list[Program]) -> int:
    """Largest cash assistance per stack group, across eligible and likely programs.

    Programs in the same stack_group (e.g. MHC's DPA products) can't be combined,
    so only the largest in each group counts.
    """
    by_id = {p.id: p for p in programs}
    best: dict[str, int] = {}
    for m in matches:
        if m.status not in ("eligible", "likely") or not m.amount_max:
            continue
        group = by_id[m.program_id].stack_group or m.program_id
        best[group] = max(best.get(group, 0), m.amount_max)
    return sum(best.values())
