"""Database writes and reads for reference data. Every write is an idempotent upsert."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Optional

import psycopg

from app.data.parsers import FmrResult, IncomeLimits, TractGeometry, TractStats
from app.models import Tract

TRACT_FLAGS = {"oz_1_0", "oz_2_0", "rural_flag", "usda_eligible", "qct", "dda"}


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=False)


# ---------------------------------------------------------------------------
# Tracts
# ---------------------------------------------------------------------------

def upsert_tract_stats(conn: psycopg.Connection, stats: Iterable[TractStats], acs_year: int) -> int:
    rows = [(s.tract_geoid, s.county_fips, s.median_hh_income, s.population,
             s.population_trend_pct, s.vacancy_rate, s.unemployment_rate, acs_year) for s in stats]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO tracts (tract_geoid, county_fips, median_hh_income, population,
                                population_5yr_pct, vacancy_rate, unemployment_rate, acs_year)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tract_geoid) DO UPDATE SET
                median_hh_income   = EXCLUDED.median_hh_income,
                population         = EXCLUDED.population,
                population_5yr_pct = EXCLUDED.population_5yr_pct,
                vacancy_rate       = EXCLUDED.vacancy_rate,
                unemployment_rate  = EXCLUDED.unemployment_rate,
                acs_year           = EXCLUDED.acs_year,
                updated_at         = now()
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def upsert_tract_geometries(conn: psycopg.Connection, geoms: Iterable[TractGeometry]) -> int:
    rows = [(g.tract_geoid, g.county_fips, json.dumps(g.geojson)) for g in geoms]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO tracts (tract_geoid, county_fips, geom)
            VALUES (%s, %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))
            ON CONFLICT (tract_geoid) DO UPDATE SET geom = EXCLUDED.geom, updated_at = now()
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def set_tract_flag(conn: psycopg.Connection, flag: str, geoids: set[str], *,
                   state_fips: str, reset_others: bool = True) -> tuple[int, int]:
    """Set `flag` TRUE for the listed tracts (and FALSE for the rest of the state when
    `reset_others`, because published lists are authoritative). Tracts not yet in the
    table are inserted so the flag isn't lost. Returns (set_true, newly_inserted)."""
    if flag not in TRACT_FLAGS:
        raise ValueError(f"unknown flag {flag!r}; expected one of {sorted(TRACT_FLAGS)}")
    geoids = {g for g in geoids if g.startswith(state_fips)}
    with conn.cursor() as cur:
        if reset_others:
            cur.execute(f"UPDATE tracts SET {flag} = FALSE, updated_at = now() "
                        f"WHERE tract_geoid LIKE %s AND {flag} IS DISTINCT FROM FALSE",
                        (state_fips + "%",))
        cur.execute("SELECT tract_geoid FROM tracts WHERE tract_geoid = ANY(%s)", (list(geoids),))
        existing = {r[0] for r in cur.fetchall()}
        new = sorted(geoids - existing)
        cur.executemany("INSERT INTO tracts (tract_geoid, county_fips) VALUES (%s, %s)",
                        [(g, g[:5]) for g in new])
        cur.execute(f"UPDATE tracts SET {flag} = TRUE, updated_at = now() WHERE tract_geoid = ANY(%s)",
                    (list(geoids),))
    conn.commit()
    return len(geoids), len(new)


def set_flag_for_counties(conn: psycopg.Connection, flag: str, county_fips: set[str]) -> int:
    """For county-level designations (e.g. nonmetro DDAs): flag every loaded tract in those counties."""
    if flag not in TRACT_FLAGS:
        raise ValueError(f"unknown flag {flag!r}")
    with conn.cursor() as cur:
        cur.execute(f"UPDATE tracts SET {flag} = TRUE, updated_at = now() WHERE county_fips = ANY(%s)",
                    (list(county_fips),))
        n = cur.rowcount
    conn.commit()
    return n


def read_geoid_csv(path: Path, geoid_col: str, filter_col: Optional[str] = None,
                   filter_values: Optional[set[str]] = None, width: int = 11) -> set[str]:
    """Read GEOIDs (zero-padded) from a CSV, optionally keeping rows where filter_col is
    in filter_values (case-insensitive) — e.g. a 'rural' column equal to 'yes'/'1'/'true'."""
    out: set[str] = set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = {c.lower(): c for c in reader.fieldnames or []}
        g = cols.get(geoid_col.lower())
        if not g:
            raise ValueError(f"column {geoid_col!r} not in {reader.fieldnames}")
        fc = cols.get(filter_col.lower()) if filter_col else None
        if filter_col and not fc:
            raise ValueError(f"column {filter_col!r} not in {reader.fieldnames}")
        wanted = {v.lower() for v in (filter_values or set())}
        for row in reader:
            raw = (row[g] or "").strip().split(".")[0]
            if not raw:
                continue
            if fc and (row[fc] or "").strip().lower() not in wanted:
                continue
            out.add(raw.zfill(width))
    return out


# ---------------------------------------------------------------------------
# HUD
# ---------------------------------------------------------------------------

def upsert_fmr(conn: psycopg.Connection, county_fips: str, area_code: str, fmr: FmrResult) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rent_benchmarks (area_code, bedrooms, fmr, fiscal_year)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (area_code, bedrooms, fiscal_year) DO UPDATE SET fmr = EXCLUDED.fmr
            """,
            [(area_code, beds, rent, fmr.fiscal_year) for beds, rent in fmr.rents.items()],
        )
        cur.execute(
            """
            INSERT INTO county_fmr_areas (county_fips, area_code, fiscal_year) VALUES (%s, %s, %s)
            ON CONFLICT (county_fips, fiscal_year) DO UPDATE SET area_code = EXCLUDED.area_code
            """,
            (county_fips, area_code, fmr.fiscal_year),
        )
    conn.commit()
    return len(fmr.rents)


def upsert_income_limits(conn: psycopg.Connection, area_code: str, il: IncomeLimits) -> int:
    rows = [(area_code, il.fiscal_year, size, pct, amt)
            for pct, sizes in il.limits.items() for size, amt in sizes.items()]
    rows += [(area_code, il.fiscal_year, size, 100, amt)
             for size, amt in il.ami_by_household_size().items()]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO income_limits (area_code, fiscal_year, household_size, pct_ami, amount)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (area_code, fiscal_year, household_size, pct_ami)
            DO UPDATE SET amount = EXCLUDED.amount
            """,
            rows,
        )
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Reads used by the analyzer
# ---------------------------------------------------------------------------

def get_tract(conn: psycopg.Connection, geoid: str) -> Optional[Tract]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT tract_geoid, county_fips, median_hh_income, population_5yr_pct, vacancy_rate,
                      unemployment_rate, oz_1_0, oz_2_0, rural_flag, usda_eligible, qct, dda
               FROM tracts WHERE tract_geoid = %s""",
            (geoid,),
        )
        r = cur.fetchone()
    if not r:
        return None
    num = lambda v: float(v) if v is not None else None
    return Tract(tract_geoid=r[0], county_fips=r[1], median_hh_income=r[2],
                 population_5yr_pct=num(r[3]), vacancy_rate=num(r[4]), unemployment_rate=num(r[5]),
                 oz_1_0=r[6], oz_2_0=r[7], rural_flag=r[8], usda_eligible=r[9], qct=r[10], dda=r[11])


def get_tract_at(conn: psycopg.Connection, lon: float, lat: float) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT tract_geoid FROM tracts WHERE geom IS NOT NULL AND "
                    "ST_Contains(geom, ST_SetSRID(ST_Point(%s, %s), 4326)) LIMIT 1", (lon, lat))
        r = cur.fetchone()
    return r[0] if r else None


def get_county_reference(conn: psycopg.Connection, county_fips: str) -> dict:
    """Latest FMRs by bedroom count and 100% AMI by household size for a county."""
    with conn.cursor() as cur:
        cur.execute("SELECT area_code, fiscal_year FROM county_fmr_areas WHERE county_fips = %s "
                    "ORDER BY fiscal_year DESC LIMIT 1", (county_fips,))
        r = cur.fetchone()
        if not r:
            return {"fmr": {}, "area_median_income": None, "fiscal_year": None}
        area, year = r
        cur.execute("SELECT bedrooms, fmr FROM rent_benchmarks WHERE area_code = %s AND fiscal_year = %s",
                    (area, year))
        fmr = {b: f for b, f in cur.fetchall()}
        cur.execute("SELECT household_size, amount FROM income_limits WHERE area_code = %s AND "
                    "pct_ami = 100 AND fiscal_year = (SELECT max(fiscal_year) FROM income_limits "
                    "WHERE area_code = %s)", (area, area))
        ami = {s: a for s, a in cur.fetchall()} or None
    return {"fmr": fmr, "area_median_income": ami, "fiscal_year": year}
