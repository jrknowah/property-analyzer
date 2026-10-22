"""Sample requests for tests and local demos.

All addresses, prices and tract values are ILLUSTRATIVE placeholders, not real
listings or real ACS/OZ figures. Replace with loaded data once the pipeline runs.
"""
from datetime import date

from app.models import (AnalyzeRequest, AreaEvent, BudgetProfile, Financing,
                        Property, RentAssumptions, Strategy, Tract)

AS_OF = date(2026, 10, 22)


def gulf_coast_rental() -> AnalyzeRequest:
    """Duplex in Jackson County, Zone AE."""
    return AnalyzeRequest(
        strategy=Strategy.rental,
        as_of=AS_OF,
        property=Property(
            parcel_id="sample-jackson-001", address="100 Sample Dr", city="Gautier",
            county_fips="28059", price=185_000, property_type="multifamily", units=2,
            bedrooms=2, zoning_type="residential", fema_zone="AE", annual_tax=2_100,
            tract=Tract(tract_geoid="28059040100", county_fips="28059",
                        median_hh_income=58_000, population_5yr_pct=2.5,
                        vacancy_rate=11, unemployment_rate=4.5),
            events=[AreaEvent(event_type="ed_project", title="Shipyard hiring expansion",
                              distance_mi=2.4, impact="positive")],
        ),
        budget=BudgetProfile(max_price=220_000, cash_available=55_000, target_coc_pct=8,
                             financing=Financing.conventional, interest_rate_pct=7.0),
        rent=RentAssumptions(fmr_per_unit=1_150),
    )


def aberdeen_rural_oz_land() -> AnalyzeRequest:
    """Vacant acre in Monroe County, hypothetically in a rural OZ 2.0 tract."""
    return AnalyzeRequest(
        strategy=Strategy.tax,
        as_of=date(2027, 2, 1),
        property=Property(
            parcel_id="sample-monroe-001", address="0 Sample Rd", city="Aberdeen",
            county_fips="28095", price=25_000, property_type="land", units=1,
            zoning_type="residential", fema_zone="X", annual_tax=180,
            tract=Tract(tract_geoid="28095950100", county_fips="28095",
                        median_hh_income=38_000, population_5yr_pct=-3.0,
                        vacancy_rate=18, unemployment_rate=6.5,
                        oz_2_0=True, rural_flag=True, usda_eligible=True, qct=True),
        ),
        budget=BudgetProfile(max_price=60_000, cash_available=60_000, financing=Financing.cash,
                             has_capital_gains=True),
    )


def golden_triangle_first_time_buyer() -> AnalyzeRequest:
    """First-time buyer, single-family home in Lowndes County."""
    return AnalyzeRequest(
        strategy=Strategy.personal,
        as_of=AS_OF,
        property=Property(
            parcel_id="sample-lowndes-001", address="200 Sample Ave", city="Columbus",
            county_fips="28087", price=165_000, property_type="single_family", units=1,
            bedrooms=3, zoning_type="residential", fema_zone="X", annual_tax=1_400,
            tract=Tract(tract_geoid="28087000100", county_fips="28087",
                        median_hh_income=47_000, population_5yr_pct=1.0,
                        vacancy_rate=12, unemployment_rate=5.0, usda_eligible=True),
        ),
        budget=BudgetProfile(max_price=180_000, cash_available=6_000, financing=Financing.fha,
                             interest_rate_pct=6.5, household_size=3, household_income=52_000,
                             first_time_buyer=True, veteran=False, occupation="teacher"),
    )
