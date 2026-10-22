"""Address -> analysis context: geocode, tract, flood zone, HUD rents and AMI.

This is the bridge from "user typed an address" to the engine's Property model,
until full parcel data (owner, assessed value, zoning) arrives from the parcel vendor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import psycopg

from app.data import clients, parsers
from app.data.loaders import get_county_reference, get_tract, get_tract_at
from app.models import Tract


@dataclass
class AddressContext:
    matched_address: str
    lon: float
    lat: float
    county_fips: str
    tract: Tract
    fema_zone: Optional[str]
    fmr_by_bedrooms: dict[int, int]
    area_median_income: Optional[dict[int, int]]
    warnings: list[str] = field(default_factory=list)

    def fmr_for(self, bedrooms: Optional[int]) -> Optional[int]:
        if bedrooms is None or not self.fmr_by_bedrooms:
            return None
        return self.fmr_by_bedrooms.get(min(max(bedrooms, 0), 4))


def lookup_address(conn: psycopg.Connection, address: str, *, flood: bool = True) -> Optional[AddressContext]:
    geo = parsers.parse_geocoder(clients.geocode_address(address))
    if geo is None:
        return None
    warnings: list[str] = []

    geoid = geo.tract_geoid or get_tract_at(conn, geo.lon, geo.lat)
    tract = get_tract(conn, geoid) if geoid else None
    county = geo.county_fips or (geoid[:5] if geoid else "")
    if tract is None:
        warnings.append("Tract data not loaded; run the ACS loader for this county.")
        tract = Tract(tract_geoid=geoid or "", county_fips=county)

    zone = None
    if flood:
        try:
            zone = parsers.parse_nfhl_point(clients.fetch_flood_zone_at(geo.lon, geo.lat))
            if zone is None:
                warnings.append("No FEMA flood zone mapped at this point.")
        except clients.DataSourceError as e:
            warnings.append(f"Flood lookup failed: {e}")

    ref = get_county_reference(conn, county)
    if not ref["fmr"]:
        warnings.append("HUD rents not loaded for this county; run the HUD loader.")

    return AddressContext(
        matched_address=geo.matched_address, lon=geo.lon, lat=geo.lat, county_fips=county,
        tract=tract, fema_zone=zone, fmr_by_bedrooms=ref["fmr"],
        area_median_income=ref["area_median_income"], warnings=warnings,
    )
