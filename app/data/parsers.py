"""Pure parsers: raw API payload -> clean rows. No network, no database.

Kept separate from the HTTP clients so they can be unit-tested against recorded
payloads and reused when an API changes shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Census ACS
# ---------------------------------------------------------------------------

ACS_VARS = {
    "B01003_001E": "population",
    "B19013_001E": "median_hh_income",
    "B25002_001E": "housing_units",
    "B25002_003E": "vacant_units",
    "B23025_003E": "civilian_labor_force",
    "B23025_005E": "unemployed",
}


def _acs_num(v: Any) -> Optional[float]:
    """ACS returns strings; negative sentinels (e.g. -666666666) mean 'not available'."""
    if v in (None, "", "null"):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return None if n < 0 else n


def _pct(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or not den:
        return None
    return round(num / den * 100, 2)


@dataclass
class TractStats:
    tract_geoid: str
    county_fips: str
    population: Optional[int]
    median_hh_income: Optional[int]
    vacancy_rate: Optional[float]
    unemployment_rate: Optional[float]
    population_trend_pct: Optional[float] = None


def parse_acs(payload: list[list[str]]) -> dict[str, TractStats]:
    """Parse an ACS 5-year tract response (header row + data rows)."""
    header, *rows = payload
    idx = {name: i for i, name in enumerate(header)}
    for col in ("state", "county", "tract", *ACS_VARS):
        if col not in idx:
            raise ValueError(f"ACS response missing column {col!r}")
    out: dict[str, TractStats] = {}
    for row in rows:
        get = lambda var: _acs_num(row[idx[var]])
        state, county, tract = row[idx["state"]], row[idx["county"]], row[idx["tract"]]
        geoid = f"{state}{county}{tract}"
        pop = get("B01003_001E")
        inc = get("B19013_001E")
        out[geoid] = TractStats(
            tract_geoid=geoid,
            county_fips=f"{state}{county}",
            population=int(pop) if pop is not None else None,
            median_hh_income=int(inc) if inc is not None else None,
            vacancy_rate=_pct(get("B25002_003E"), get("B25002_001E")),
            unemployment_rate=_pct(get("B23025_005E"), get("B23025_003E")),
        )
    return out


def add_population_trend(current: dict[str, TractStats], baseline: dict[str, TractStats]) -> int:
    """Fill population_trend_pct where the tract exists in both vintages. Returns count filled."""
    filled = 0
    for geoid, cur in current.items():
        base = baseline.get(geoid)
        if base and base.population and cur.population is not None:
            cur.population_trend_pct = round((cur.population / base.population - 1) * 100, 2)
            filled += 1
    return filled


# ---------------------------------------------------------------------------
# Census geocoder
# ---------------------------------------------------------------------------

@dataclass
class GeocodeResult:
    matched_address: str
    lon: float
    lat: float
    tract_geoid: Optional[str]
    county_fips: Optional[str]


def parse_geocoder(payload: dict) -> Optional[GeocodeResult]:
    matches = payload.get("result", {}).get("addressMatches", [])
    if not matches:
        return None
    m = matches[0]
    geos = m.get("geographies", {})
    tracts = geos.get("Census Tracts") or geos.get("Census Tracts 2020") or []
    counties = geos.get("Counties") or []
    tract = tracts[0]["GEOID"] if tracts else None
    county = counties[0]["GEOID"] if counties else (tract[:5] if tract else None)
    return GeocodeResult(
        matched_address=m.get("matchedAddress", ""),
        lon=float(m["coordinates"]["x"]),
        lat=float(m["coordinates"]["y"]),
        tract_geoid=tract,
        county_fips=county,
    )


# ---------------------------------------------------------------------------
# TIGERweb tract geometry (GeoJSON)
# ---------------------------------------------------------------------------

@dataclass
class TractGeometry:
    tract_geoid: str
    county_fips: str
    geojson: dict


def parse_tigerweb_tracts(payload: dict) -> list[TractGeometry]:
    out = []
    for f in payload.get("features", []):
        props = f.get("properties", {})
        geoid = props.get("GEOID") or f"{props.get('STATE')}{props.get('COUNTY')}{props.get('TRACT')}"
        if not f.get("geometry") or len(geoid) != 11:
            continue
        out.append(TractGeometry(tract_geoid=geoid, county_fips=geoid[:5], geojson=f["geometry"]))
    return out


# ---------------------------------------------------------------------------
# HUD Fair Market Rents and Income Limits
# ---------------------------------------------------------------------------

FMR_KEYS = {
    0: ("Efficiency", "efficiency"),
    1: ("One-Bedroom", "one_bedroom"),
    2: ("Two-Bedroom", "two_bedroom"),
    3: ("Three-Bedroom", "three_bedroom"),
    4: ("Four-Bedroom", "four_bedroom"),
}


@dataclass
class FmrResult:
    area_name: str
    fiscal_year: int
    rents: dict[int, int]          # bedrooms -> monthly FMR
    small_area: bool = False


def parse_hud_fmr(payload: dict) -> FmrResult:
    """HUD FMR API, county entity (e.g. 2809599999).

    `basicdata` is a dict for regular FMR areas, or a list of ZIP rows for
    Small Area FMR metros — then the 'MSA level' row is used.
    """
    data = payload["data"]
    basic = data["basicdata"]
    small_area = isinstance(basic, list)
    if small_area:
        row = next((r for r in basic if str(r.get("zip_code", "")).lower() == "msa level"), basic[0])
    else:
        row = basic
    rents = {}
    for beds, keys in FMR_KEYS.items():
        for k in keys:
            if row.get(k) not in (None, ""):
                rents[beds] = int(float(row[k]))
                break
    year = row.get("year") or data.get("year")
    return FmrResult(
        area_name=data.get("area_name") or data.get("metro_name") or data.get("county_name", ""),
        fiscal_year=int(year),
        rents=rents,
        small_area=small_area,
    )


@dataclass
class IncomeLimits:
    area_name: str
    fiscal_year: int
    median_income: Optional[int]
    # pct_ami -> household_size -> amount
    limits: dict[int, dict[int, int]] = field(default_factory=dict)

    def ami_by_household_size(self) -> dict[int, int]:
        """100% AMI by household size, derived from the 50% (very low) limits.

        A screening approximation: HUD applies caps and floors, so exact
        program eligibility should use each program's published table.
        """
        return {size: amt * 2 for size, amt in self.limits.get(50, {}).items()}


IL_GROUPS = {30: ("extremely_low", "il30_p"), 50: ("very_low", "il50_p"), 80: ("low", "il80_p")}


def parse_hud_il(payload: dict) -> IncomeLimits:
    data = payload["data"]
    limits: dict[int, dict[int, int]] = {}
    for pct, (group, prefix) in IL_GROUPS.items():
        block = data.get(group) or {}
        sizes = {}
        for size in range(1, 9):
            v = block.get(f"{prefix}{size}")
            if v not in (None, ""):
                sizes[size] = int(float(v))
        if sizes:
            limits[pct] = sizes
    mi = data.get("median_income")
    return IncomeLimits(
        area_name=data.get("area_name", ""),
        fiscal_year=int(data.get("year")),
        median_income=int(float(mi)) if mi not in (None, "") else None,
        limits=limits,
    )


# ---------------------------------------------------------------------------
# FEMA NFHL flood hazard zones
# ---------------------------------------------------------------------------

def normalize_fema_zone(fld_zone: Optional[str], subtype: Optional[str]) -> Optional[str]:
    """Map NFHL FLD_ZONE + ZONE_SUBTY to the codes the scoring engine uses."""
    if not fld_zone:
        return None
    z = fld_zone.strip().upper()
    sub = (subtype or "").upper()
    if z == "X" and "0.2 PCT" in sub:
        return "X500"
    if z == "AREA NOT INCLUDED" or z == "D":
        return None
    return z


SFHA_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}
# Most to least severe, used when a point touches several polygons.
ZONE_SEVERITY = ["VE", "V", "AE", "A", "AO", "AH", "AR", "A99", "X500", "X"]


def parse_nfhl_point(payload: dict) -> Optional[str]:
    """Pick the most severe zone among features returned for a point query."""
    zones = []
    for f in payload.get("features", []):
        attrs = f.get("attributes") or f.get("properties") or {}
        z = normalize_fema_zone(attrs.get("FLD_ZONE"), attrs.get("ZONE_SUBTY"))
        if z:
            zones.append(z)
    if not zones:
        return None
    rank = {z: i for i, z in enumerate(ZONE_SEVERITY)}
    return min(zones, key=lambda z: rank.get(z, len(rank)))
