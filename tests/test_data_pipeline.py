"""Data pipeline tests.

Parser tests run offline against payloads shaped like each API's documented
response (values are illustrative). Database tests run when TEST_DATABASE_URL
points at an empty PostGIS database with db/schema.sql applied; otherwise skipped.
"""
import os
from pathlib import Path

import pytest

from app.data import clients, loaders, parsers

# --- recorded-shape payloads ------------------------------------------------

ACS_HEADER = ["NAME", "B01003_001E", "B19013_001E", "B25002_001E", "B25002_003E",
              "B23025_003E", "B23025_005E", "state", "county", "tract"]


def acs_payload(pop_a="4200", pop_b="3100"):
    return [
        ACS_HEADER,
        ["Census Tract 9501; Monroe County; Mississippi", pop_a, "38500", "2000", "360", "1800", "117",
         "28", "095", "950100"],
        ["Census Tract 9502; Monroe County; Mississippi", pop_b, "-666666666", "1500", "150", "1300", "52",
         "28", "095", "950200"],
    ]


TIGER = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"GEOID": "28095950100"},
     "geometry": {"type": "Polygon", "coordinates": [[[-88.60, 33.80], [-88.50, 33.80],
                                                      [-88.50, 33.85], [-88.60, 33.85], [-88.60, 33.80]]]}},
    {"type": "Feature", "properties": {"GEOID": "28095950200"},
     "geometry": {"type": "Polygon", "coordinates": [[[-88.50, 33.80], [-88.40, 33.80],
                                                      [-88.40, 33.85], [-88.50, 33.85], [-88.50, 33.80]]]}},
]}

HUD_FMR = {"data": {"county_name": "Monroe County", "area_name": "Monroe County, MS",
                    "basicdata": {"Efficiency": 610, "One-Bedroom": 650, "Two-Bedroom": 810,
                                  "Three-Bedroom": 1050, "Four-Bedroom": 1180, "year": "2026"}}}

HUD_FMR_SMALL_AREA = {"data": {"metro_name": "Gulfport-Biloxi, MS HUD Metro FMR Area", "year": "2026",
                               "basicdata": [
                                   {"zip_code": "MSA level", "Efficiency": 900, "One-Bedroom": 950,
                                    "Two-Bedroom": 1150, "Three-Bedroom": 1500, "Four-Bedroom": 1700},
                                   {"zip_code": "39553", "Efficiency": 880, "One-Bedroom": 930,
                                    "Two-Bedroom": 1120, "Three-Bedroom": 1460, "Four-Bedroom": 1650}]}}

HUD_IL = {"data": {"area_name": "Monroe County, MS", "year": "2026", "median_income": 62000,
                   "very_low": {f"il50_p{i}": 22000 + 2500 * (i - 1) for i in range(1, 9)},
                   "extremely_low": {f"il30_p{i}": 13200 + 1500 * (i - 1) for i in range(1, 9)},
                   "low": {f"il80_p{i}": 35200 + 4000 * (i - 1) for i in range(1, 9)}}}

GEOCODER = {"result": {"addressMatches": [{
    "matchedAddress": "123 MAIN ST, ABERDEEN, MS, 39730",
    "coordinates": {"x": -88.55, "y": 33.82},
    "geographies": {"Census Tracts": [{"GEOID": "28095950100"}], "Counties": [{"GEOID": "28095"}]}}]}}

NFHL = {"features": [{"attributes": {"FLD_ZONE": "X", "ZONE_SUBTY": "0.2 PCT ANNUAL CHANCE FLOOD HAZARD"}},
                     {"attributes": {"FLD_ZONE": "AE", "ZONE_SUBTY": None}}]}


# --- parsers ----------------------------------------------------------------

def test_parse_acs_rates_and_sentinels():
    stats = parsers.parse_acs(acs_payload())
    a = stats["28095950100"]
    assert a.county_fips == "28095" and a.population == 4200 and a.median_hh_income == 38500
    assert a.vacancy_rate == 18.0 and a.unemployment_rate == 6.5
    assert stats["28095950200"].median_hh_income is None   # -666666666 = not available


def test_population_trend_only_for_matching_geoids():
    cur = parsers.parse_acs(acs_payload("4200", "3100"))
    base = parsers.parse_acs(acs_payload("4000", "3100"))
    del base["28095950200"]
    assert parsers.add_population_trend(cur, base) == 1
    assert cur["28095950100"].population_trend_pct == 5.0
    assert cur["28095950200"].population_trend_pct is None


def test_parse_acs_rejects_missing_columns():
    with pytest.raises(ValueError):
        parsers.parse_acs([["NAME", "state"], ["x", "28"]])


def test_parse_hud_fmr_regular_and_small_area():
    f = parsers.parse_hud_fmr(HUD_FMR)
    assert f.fiscal_year == 2026 and f.rents == {0: 610, 1: 650, 2: 810, 3: 1050, 4: 1180}
    s = parsers.parse_hud_fmr(HUD_FMR_SMALL_AREA)
    assert s.small_area and s.rents[2] == 1150      # MSA-level row, not a ZIP row


def test_parse_hud_il_and_ami():
    il = parsers.parse_hud_il(HUD_IL)
    assert il.limits[80][4] == 47200 and il.limits[50][1] == 22000
    assert il.ami_by_household_size()[4] == 59000   # 2 x very-low 4-person


def test_parse_geocoder():
    g = parsers.parse_geocoder(GEOCODER)
    assert g.tract_geoid == "28095950100" and g.county_fips == "28095" and g.lon == -88.55
    assert parsers.parse_geocoder({"result": {"addressMatches": []}}) is None


def test_nfhl_picks_most_severe_zone():
    assert parsers.parse_nfhl_point(NFHL) == "AE"
    assert parsers.normalize_fema_zone("X", "0.2 PCT ANNUAL CHANCE FLOOD HAZARD") == "X500"
    assert parsers.parse_nfhl_point({"features": []}) is None


def test_read_geoid_csv_pads_and_filters(tmp_path):
    p = tmp_path / "oz.csv"
    p.write_text("GEOID,Rural\n28095950100,Yes\n28095950200.0,No\n8001000100,yes\n")
    assert loaders.read_geoid_csv(p, "geoid") == {"28095950100", "28095950200", "08001000100"}
    assert loaders.read_geoid_csv(p, "GEOID", "rural", {"yes"}) == {"28095950100", "08001000100"}


def test_hud_requires_token(monkeypatch):
    monkeypatch.setattr(clients.config, "HUD_API_TOKEN", None)
    with pytest.raises(clients.DataSourceError):
        clients.fetch_hud_fmr("28095")


# --- database (PostGIS) -----------------------------------------------------

DB_URL = os.getenv("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL to a PostGIS database")


@pytest.fixture
def conn():
    c = loaders.connect(DB_URL)
    with c.cursor() as cur:
        cur.execute("TRUNCATE tracts, rent_benchmarks, county_fmr_areas, income_limits CASCADE")
    c.commit()
    yield c
    c.close()


@pytest.fixture
def fake_apis(monkeypatch):
    monkeypatch.setattr(clients, "fetch_acs_tracts",
                        lambda year, county, v: acs_payload() if year != 2021 else acs_payload("4000", "3000"))
    monkeypatch.setattr(clients, "fetch_tract_geometries", lambda county: TIGER)
    monkeypatch.setattr(clients, "fetch_hud_fmr", lambda county: HUD_FMR)
    monkeypatch.setattr(clients, "fetch_hud_income_limits", lambda county: HUD_IL)
    monkeypatch.setattr(clients, "geocode_address", lambda a: GEOCODER)
    monkeypatch.setattr(clients, "fetch_flood_zone_at", lambda lon, lat: NFHL)
    monkeypatch.setattr(clients.config, "HUD_API_TOKEN", "test")
    monkeypatch.setattr(clients.config, "ACS_COMPARE_YEAR", 2021)


@needs_db
def test_cli_all_then_lookup(conn, fake_apis, capsys):
    from app.data import cli
    cli.main(["--db", DB_URL, "all", "--counties", "28095"])
    out = capsys.readouterr().out
    assert "28095 Monroe" in out

    t = loaders.get_tract(conn, "28095950100")
    assert t.vacancy_rate == 18.0 and t.population_5yr_pct == 5.0
    assert loaders.get_tract_at(conn, -88.55, 33.82) == "28095950100"   # point-in-polygon

    ref = loaders.get_county_reference(conn, "28095")
    assert ref["fmr"][2] == 810 and ref["area_median_income"][4] == 59000

    from app.data.enrich import lookup_address
    ctx = lookup_address(conn, "123 Main St, Aberdeen, MS")
    assert ctx.fema_zone == "AE" and ctx.fmr_for(2) == 810 and not ctx.warnings


@needs_db
def test_flags_are_authoritative(conn, tmp_path):
    loaders.upsert_tract_geometries(conn, parsers.parse_tigerweb_tracts(TIGER))
    p = tmp_path / "oz.csv"
    p.write_text("GEOID\n28095950100\n28087000100\n")
    n, new = loaders.set_tract_flag(conn, "oz_2_0", loaders.read_geoid_csv(p, "GEOID"), state_fips="28")
    assert (n, new) == (2, 1)                       # Lowndes tract inserted so the flag isn't lost
    p.write_text("GEOID\n28095950200\n")
    loaders.set_tract_flag(conn, "oz_2_0", loaders.read_geoid_csv(p, "GEOID"), state_fips="28")
    assert not loaders.get_tract(conn, "28095950100").oz_2_0   # cleared: list is authoritative
    assert loaders.get_tract(conn, "28095950200").oz_2_0


@needs_db
def test_upserts_are_idempotent(conn):
    for _ in range(2):
        loaders.upsert_tract_stats(conn, parsers.parse_acs(acs_payload()).values(), 2024)
        loaders.upsert_fmr(conn, "28095", "2809599999", parsers.parse_hud_fmr(HUD_FMR))
        loaders.upsert_income_limits(conn, "2809599999", parsers.parse_hud_il(HUD_IL))
    with conn.cursor() as cur:
        cur.execute("SELECT (SELECT count(*) FROM tracts), (SELECT count(*) FROM rent_benchmarks), "
                    "(SELECT count(*) FROM income_limits)")
        assert cur.fetchone() == (2, 5, 32)          # 8 sizes x (30, 50, 80, 100)


@needs_db
def test_api_analyze_by_address(conn, fake_apis, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    from app.data import cli
    cli.main(["--db", DB_URL, "all", "--counties", "28095"])
    monkeypatch.setattr(main.data_config, "DATABASE_URL", DB_URL)
    r = TestClient(main.app).post("/analyze/address", json={
        "address": "123 Main St, Aberdeen, MS", "price": 120000, "strategy": "rental",
        "bedrooms": 2, "budget": {"max_price": 150000, "cash_available": 40000},
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["matched_address"].startswith("123 MAIN ST")
    assert d["analysis"]["deal"]["assumptions"]["rent_source"] == "hud_fmr"
    assert d["analysis"]["deal"]["assumptions"]["rent_per_unit"] == 810
    risk = next(f for f in d["analysis"]["factors"] if f["factor"] == "risk")
    assert risk["score"] <= 35                                  # AE from NFHL
