"""Deal calculator: cash required, NOI, cap rate, cash-on-cash, DSCR.

Defaults are deliberately conservative placeholders and are returned in
`assumptions` so the UI can show and let the user edit every one of them.
"""
from __future__ import annotations

from app.models import AnalyzeRequest, DealMetrics, Financing, Strategy

# Minimum down payment as a share of price, by financing type.
DOWN_PAYMENT_PCT = {
    Financing.cash: 1.00,
    Financing.conventional: 0.20,   # investor default; owner-occupants use 0.05 below
    Financing.fha: 0.035,
    Financing.va: 0.0,
    Financing.usda: 0.0,
    Financing.dscr: 0.25,
}
OWNER_OCCUPANT_CONVENTIONAL_DOWN = 0.05
CLOSING_COST_PCT = 0.03
DEFAULT_TAX_RATE = 0.010           # placeholder when the assessor figure is missing

# Insurance placeholders: base rate x flood multiplier x coastal wind multiplier.
INSURANCE_BASE_RATE = 0.006
FLOOD_MULTIPLIER = {"VE": 3.5, "V": 3.5, "AE": 2.0, "A": 2.0, "AO": 1.8, "AH": 1.8,
                    "X500": 1.2, "B": 1.2, "X": 1.0, "C": 1.0}
COASTAL_COUNTIES = {"28045", "28047", "28059"}   # Hancock, Harrison, Jackson
COASTAL_WIND_MULTIPLIER = 1.6


def monthly_payment(principal: float, annual_rate_pct: float, years: int) -> float:
    if principal <= 0:
        return 0.0
    r = annual_rate_pct / 100 / 12
    n = years * 12
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)


def estimate_insurance(price: float, fema_zone: str | None, county_fips: str) -> float:
    mult = FLOOD_MULTIPLIER.get((fema_zone or "X").upper(), 1.0)
    if county_fips in COASTAL_COUNTIES:
        mult *= COASTAL_WIND_MULTIPLIER
    return round(price * INSURANCE_BASE_RATE * mult, 2)


def compute_deal(req: AnalyzeRequest) -> DealMetrics:
    p, b, r = req.property, req.budget, req.rent
    owner_occupied = req.strategy == Strategy.personal

    down_pct = DOWN_PAYMENT_PCT[b.financing]
    if b.financing == Financing.conventional and owner_occupied:
        down_pct = OWNER_OCCUPANT_CONVENTIONAL_DOWN

    price = p.price
    down = round(price * down_pct, 2)
    closing = round(price * CLOSING_COST_PCT, 2)
    loan = round(price - down, 2)
    all_in = round(price + closing + p.est_rehab, 2)
    cash_required = round(down + closing + p.est_rehab, 2)

    debt_service = round(monthly_payment(loan, b.interest_rate_pct, b.loan_term_years) * 12, 2)

    rent_per_unit = r.monthly_rent_per_unit or r.fmr_per_unit or 0.0
    rent_source = ("user" if r.monthly_rent_per_unit
                   else "hud_fmr" if r.fmr_per_unit else "none")
    gross_rent = round(rent_per_unit * p.units * 12, 2)
    effective_rent = gross_rent * (1 - r.vacancy_pct / 100)

    taxes = p.annual_tax if p.annual_tax is not None else round(price * DEFAULT_TAX_RATE, 2)
    insurance = (r.annual_insurance if r.annual_insurance is not None
                 else estimate_insurance(price, p.fema_zone, p.county_fips))
    mgmt = effective_rent * r.management_pct / 100
    maint = effective_rent * r.maintenance_pct / 100
    opex = taxes + insurance + mgmt + maint + r.other_annual_opex

    if gross_rent > 0:
        noi = round(effective_rent - opex, 2)
        cap_rate = round(noi / price * 100, 2)
        cash_flow = round(noi - debt_service, 2)
        coc = round(cash_flow / cash_required * 100, 2) if cash_required > 0 else None
        dscr = round(noi / debt_service, 2) if debt_service > 0 else None
    else:
        # No income (owner-occupied, vacant land): income metrics don't apply.
        noi = cap_rate = cash_flow = coc = dscr = None
    rent_vs_fmr = (round((rent_per_unit / r.fmr_per_unit - 1) * 100, 1)
                   if r.fmr_per_unit and rent_per_unit else None)

    return DealMetrics(
        all_in_cost=all_in,
        down_payment=down,
        closing_costs=closing,
        cash_required=cash_required,
        loan_amount=loan,
        annual_debt_service=debt_service,
        gross_rent=gross_rent,
        noi=noi,
        cap_rate_pct=cap_rate,
        cash_flow_annual=cash_flow,
        cash_on_cash_pct=coc,
        dscr=dscr,
        rent_vs_fmr_pct=rent_vs_fmr,
        assumptions={
            "down_payment_pct": down_pct * 100,
            "closing_cost_pct": CLOSING_COST_PCT * 100,
            "rent_per_unit": rent_per_unit,
            "rent_source": rent_source,
            "vacancy_pct": r.vacancy_pct,
            "management_pct": r.management_pct,
            "maintenance_pct": r.maintenance_pct,
            "annual_taxes": taxes,
            "taxes_source": "assessor" if p.annual_tax is not None else "estimate",
            "annual_insurance": insurance,
            "insurance_source": "user" if r.annual_insurance is not None else "estimate",
            "interest_rate_pct": b.interest_rate_pct,
            "loan_term_years": b.loan_term_years,
        },
    )
