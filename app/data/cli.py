"""Data loader CLI.

    python -m app.data.cli all                       # ACS + tract shapes + HUD for Phase 1 counties
    python -m app.data.cli acs --counties 28095 28087
    python -m app.data.cli tracts
    python -m app.data.cli hud
    python -m app.data.cli flags --flag oz_2_0 --file data/ms_oz_2_0.csv --geoid-col GEOID
    python -m app.data.cli flags --flag rural_flag --file data/ms_oz_2_0.csv --geoid-col GEOID \
                                 --filter-col Rural --filter-values yes 1 true
    python -m app.data.cli dda-counties --counties 28095 28025
    python -m app.data.cli lookup "123 Main St, Aberdeen, MS 39730"
    python -m app.data.cli status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.data import clients, config, loaders, parsers
from app.data.enrich import lookup_address


def _counties(args) -> list[str]:
    return args.counties or list(config.PHASE1_COUNTIES)


def cmd_acs(args, conn) -> None:
    variables = list(parsers.ACS_VARS)
    for county in _counties(args):
        cur = parsers.parse_acs(clients.fetch_acs_tracts(config.ACS_YEAR, county, variables))
        try:
            base = parsers.parse_acs(clients.fetch_acs_tracts(config.ACS_COMPARE_YEAR, county, variables))
            filled = parsers.add_population_trend(cur, base)
        except clients.DataSourceError as e:
            print(f"  {county}: baseline {config.ACS_COMPARE_YEAR} unavailable ({e}); trend skipped")
            filled = 0
        n = loaders.upsert_tract_stats(conn, cur.values(), config.ACS_YEAR)
        print(f"ACS {config.ACS_YEAR} {county} {config.PHASE1_COUNTIES.get(county, '')}: "
              f"{n} tracts, trend on {filled}")


def cmd_tracts(args, conn) -> None:
    for county in _counties(args):
        geoms = parsers.parse_tigerweb_tracts(clients.fetch_tract_geometries(county))
        n = loaders.upsert_tract_geometries(conn, geoms)
        print(f"Tract shapes {county}: {n}")


def cmd_hud(args, conn) -> None:
    for county in _counties(args):
        area = clients.hud_entity_id(county)
        fmr = parsers.parse_hud_fmr(clients.fetch_hud_fmr(county))
        il = parsers.parse_hud_il(clients.fetch_hud_income_limits(county))
        loaders.upsert_fmr(conn, county, area, fmr)
        loaders.upsert_income_limits(conn, area, il)
        two_br = fmr.rents.get(2)
        print(f"HUD {county} FY{fmr.fiscal_year}: 2BR FMR ${two_br}, "
              f"median income ${il.median_income}{' (small-area metro)' if fmr.small_area else ''}")


def cmd_flags(args, conn) -> None:
    geoids = loaders.read_geoid_csv(Path(args.file), args.geoid_col, args.filter_col,
                                    set(args.filter_values or []))
    n, new = loaders.set_tract_flag(conn, args.flag, geoids, state_fips=config.STATE_FIPS,
                                    reset_others=not args.keep_others)
    print(f"{args.flag}: set on {n} Mississippi tracts ({new} not previously loaded)")


def cmd_dda_counties(args, conn) -> None:
    n = loaders.set_flag_for_counties(conn, "dda", set(args.counties))
    print(f"dda: set on {n} tracts")


def cmd_lookup(args, conn) -> None:
    ctx = lookup_address(conn, args.address, flood=not args.no_flood)
    if ctx is None:
        print("No geocoder match.")
        sys.exit(1)
    out = {
        "matched_address": ctx.matched_address, "lon": ctx.lon, "lat": ctx.lat,
        "county_fips": ctx.county_fips, "fema_zone": ctx.fema_zone,
        "tract": ctx.tract.model_dump(), "fmr_by_bedrooms": ctx.fmr_by_bedrooms,
        "area_median_income": ctx.area_median_income, "warnings": ctx.warnings,
    }
    print(json.dumps(out, indent=2))


def cmd_status(args, conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.county_fips, c.name,
                   count(t.*) AS tracts,
                   count(t.geom) AS with_shape,
                   count(t.median_hh_income) AS with_acs,
                   count(*) FILTER (WHERE t.oz_2_0) AS oz2,
                   (SELECT max(fiscal_year) FROM county_fmr_areas f WHERE f.county_fips = c.county_fips) AS fmr_year
            FROM coverage_counties c LEFT JOIN tracts t ON t.county_fips = c.county_fips
            GROUP BY c.county_fips, c.name ORDER BY c.county_fips""")
        rows = cur.fetchall()
    print(f"{'county':<16}{'tracts':>7}{'shapes':>8}{'acs':>6}{'oz2':>5}{'fmr':>7}")
    for fips, name, n, shp, acs, oz2, fy in rows:
        print(f"{fips} {name:<10}{n:>7}{shp:>8}{acs:>6}{oz2:>5}{fy or '-':>7}")


def cmd_all(args, conn) -> None:
    cmd_tracts(args, conn)
    cmd_acs(args, conn)
    if config.HUD_API_TOKEN:
        cmd_hud(args, conn)
    else:
        print("HUD skipped: set HUD_API_TOKEN")
    cmd_status(args, conn)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.data.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=config.DATABASE_URL)
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn in (("all", cmd_all), ("acs", cmd_acs), ("tracts", cmd_tracts), ("hud", cmd_hud)):
        s = sub.add_parser(name)
        s.add_argument("--counties", nargs="+", help="5-digit FIPS (default: Phase 1 counties)")
        s.set_defaults(fn=fn)

    s = sub.add_parser("flags", help="Set a tract flag from a CSV of GEOIDs")
    s.add_argument("--flag", required=True, choices=sorted(loaders.TRACT_FLAGS))
    s.add_argument("--file", required=True)
    s.add_argument("--geoid-col", default="GEOID")
    s.add_argument("--filter-col")
    s.add_argument("--filter-values", nargs="+")
    s.add_argument("--keep-others", action="store_true", help="Don't clear the flag on unlisted tracts")
    s.set_defaults(fn=cmd_flags)

    s = sub.add_parser("dda-counties", help="Flag every tract in nonmetro DDA counties")
    s.add_argument("--counties", nargs="+", required=True)
    s.set_defaults(fn=cmd_dda_counties)

    s = sub.add_parser("lookup", help="Geocode an address and show its analysis context")
    s.add_argument("address")
    s.add_argument("--no-flood", action="store_true")
    s.set_defaults(fn=cmd_lookup)

    sub.add_parser("status").set_defaults(fn=cmd_status)

    args = p.parse_args(argv)
    with loaders.connect(args.db) as conn:
        args.fn(args, conn)


if __name__ == "__main__":
    main()
