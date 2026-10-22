"""Thin HTTP clients for public data APIs. Each returns the raw JSON payload;
parsing lives in app.data.parsers so it can be tested offline."""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from app.data import config

USER_AGENT = "PropertyInvestmentAnalyzer/0.1 (DreamLogic Solutions)"


class DataSourceError(RuntimeError):
    pass


def _get(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
         retries: int = 3) -> Any:
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            r = httpx.get(url, params=params, headers=h, timeout=config.HTTP_TIMEOUT,
                          follow_redirects=True)
            if r.status_code == 429 or r.status_code >= 500:
                raise DataSourceError(f"{r.status_code} from {url}")
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, DataSourceError, ValueError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise DataSourceError(f"GET {url} failed after {retries} attempts: {last}")


# --- Census -----------------------------------------------------------------

def fetch_acs_tracts(year: int, county_fips: str, variables: list[str]) -> list[list[str]]:
    params = {
        "get": ",".join(["NAME", *variables]),
        "for": "tract:*",
        "in": f"state:{county_fips[:2]} county:{county_fips[2:]}",
    }
    if config.CENSUS_API_KEY:
        params["key"] = config.CENSUS_API_KEY
    return _get(config.CENSUS_ACS_URL.format(year=year), params)


def geocode_address(address: str) -> dict:
    return _get(config.CENSUS_GEOCODER_URL, {
        "address": address,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "Census Tracts,Counties",
        "format": "json",
    })


def fetch_tract_geometries(county_fips: str) -> dict:
    return _get(config.TIGERWEB_TRACTS_URL, {
        "where": f"STATE='{county_fips[:2]}' AND COUNTY='{county_fips[2:]}'",
        "outFields": "GEOID,STATE,COUNTY,TRACT",
        "outSR": "4326",
        "f": "geojson",
    })


# --- HUD USER -----------------------------------------------------------------

def _hud_headers() -> dict:
    if not config.HUD_API_TOKEN:
        raise DataSourceError("Set HUD_API_TOKEN (free at https://www.huduser.gov/hudapi/public/register)")
    return {"Authorization": f"Bearer {config.HUD_API_TOKEN}"}


def hud_entity_id(county_fips: str) -> str:
    """HUD county entity id: 5-digit FIPS + '99999'."""
    return f"{county_fips}99999"


def fetch_hud_fmr(county_fips: str) -> dict:
    params = {"year": config.HUD_FMR_YEAR} if config.HUD_FMR_YEAR else None
    return _get(config.HUD_FMR_URL.format(entity_id=hud_entity_id(county_fips)), params, _hud_headers())


def fetch_hud_income_limits(county_fips: str) -> dict:
    params = {"year": config.HUD_FMR_YEAR} if config.HUD_FMR_YEAR else None
    return _get(config.HUD_IL_URL.format(entity_id=hud_entity_id(county_fips)), params, _hud_headers())


# --- FEMA ---------------------------------------------------------------------

def fetch_flood_zone_at(lon: float, lat: float) -> dict:
    return _get(config.FEMA_NFHL_FLOOD_URL, {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF",
        "returnGeometry": "false",
        "f": "json",
    })
