"""Data-pipeline settings. Override with environment variables."""
import os

STATE_FIPS = "28"  # Mississippi

# Phase 1 beta counties (must match coverage_counties in db/schema.sql)
PHASE1_COUNTIES = {
    "28045": "Hancock",
    "28047": "Harrison",
    "28059": "Jackson",
    "28095": "Monroe",
    "28087": "Lowndes",
    "28105": "Oktibbeha",
    "28025": "Clay",
}

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://analyzer:analyzer_dev_only@localhost:5432/analyzer")

# Census — a key is optional but raises rate limits: https://api.census.gov/data/key_signup.html
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY")
ACS_YEAR = int(os.getenv("ACS_YEAR", "2024"))           # latest ACS 5-year release
ACS_COMPARE_YEAR = int(os.getenv("ACS_COMPARE_YEAR", "2021"))  # baseline for population trend;
# keep both years on 2020 tract boundaries so GEOIDs line up.

# HUD USER — free token: https://www.huduser.gov/hudapi/public/register
HUD_API_TOKEN = os.getenv("HUD_API_TOKEN")
HUD_FMR_YEAR = os.getenv("HUD_FMR_YEAR")  # None = HUD's current year

# Endpoints (verify layer ids if a service is reorganized)
CENSUS_ACS_URL = "https://api.census.gov/data/{year}/acs/acs5"
CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
TIGERWEB_TRACTS_URL = os.getenv(
    "TIGERWEB_TRACTS_URL",
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer/0/query",
)
HUD_FMR_URL = "https://www.huduser.gov/hudapi/public/fmr/data/{entity_id}"
HUD_IL_URL = "https://www.huduser.gov/hudapi/public/il/data/{entity_id}"
FEMA_NFHL_FLOOD_URL = os.getenv(
    "FEMA_NFHL_FLOOD_URL",
    "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query",  # S_FLD_HAZ_AR
)

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "60"))
