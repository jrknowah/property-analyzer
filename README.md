# Property Investment Analyzer — backend core

Scores a property for a chosen strategy (rental, commercial, personal, tax) against a
buyer's budget, runs the deal math, and matches funding programs (MHC, USDA, FHA/VA,
Opportunity Zones 1.0/2.0, LIHTC QCT/DDA). Phase 1 covers the Gulf Coast (Hancock,
Harrison, Jackson) and Northeast Mississippi (Monroe, Lowndes, Oktibbeha, Clay).

## Run it

```bash
pip install -r requirements.txt
pytest -q
uvicorn app.main:app --reload              # http://localhost:8000/docs
curl -s localhost:8000/analyze -H 'content-type: application/json' \
     -d @examples/first_time_buyer_columbus.json | jq '.total_score, .grade, .programs[0]'
```

With Docker (API + PostGIS, schema applied on first start): `docker compose up`.

## Load real data (Phase 1 counties)

Needs a free [HUD USER token](https://www.huduser.gov/hudapi/public/register); a
[Census key](https://api.census.gov/data/key_signup.html) is optional.

```bash
docker compose up -d db
export DATABASE_URL=postgresql://analyzer:analyzer_dev_only@localhost:5432/analyzer
export HUD_API_TOKEN=...            # CENSUS_API_KEY optional
python -m app.data.cli all          # tract shapes + ACS + HUD rents/income limits, then status
python -m app.data.cli lookup "123 Main St, Aberdeen, MS 39730"   # geocode + tract + FEMA zone
```

Then `POST /analyze/address` with an address, price, strategy and budget.

Designation lists come as files — load them when published:

```bash
python -m app.data.cli flags --flag oz_2_0 --file data/ms_oz_2_0.csv --geoid-col GEOID
python -m app.data.cli flags --flag rural_flag --file data/ms_oz_2_0.csv --geoid-col GEOID \
       --filter-col Rural --filter-values yes 1 true
python -m app.data.cli flags --flag oz_1_0 --file data/oz_1_0.csv --geoid-col GEOID
python -m app.data.cli flags --flag qct --file data/qct_2026.csv --geoid-col GEOID
python -m app.data.cli dda-counties --counties 28095 28025      # nonmetro DDA counties
```

Flag lists are authoritative: loading one clears that flag on Mississippi tracts not in it
(`--keep-others` to skip). Column names vary by publisher; pass `--geoid-col` to match.

| Source | Loader | Notes |
| --- | --- | --- |
| Census ACS 5-year | `acs` | `ACS_YEAR` (default 2024) vs `ACS_COMPARE_YEAR` (2021) for population trend; both on 2020 tracts |
| TIGERweb tract shapes | `tracts` | Enables point-in-tract lookups |
| HUD FMR + income limits | `hud` | County entity ids (`FIPS99999`); Small Area FMR metros use the MSA-level row |
| FEMA NFHL | per address in `lookup` | Most severe zone at the point; 0.2% annual chance maps to `X500` |
| OZ 1.0 / 2.0, rural, QCT, USDA | `flags` | From published CSVs |

Tests: `pytest -q` runs offline; set `TEST_DATABASE_URL` to an empty PostGIS database with
`db/schema.sql` applied to also run the database tests.

## Layout

| Path | What it is |
| --- | --- |
| `db/schema.sql` | PostGIS schema: parcels, tracts, flood zones, area events, HUD tables, programs, analyses |
| `app/models.py` | Pydantic request/response models |
| `app/scoring/deal_math.py` | Cash required, NOI, cap rate, cash-on-cash, DSCR, insurance estimate |
| `app/scoring/factors.py` | Seven 0-100 factor scores with plain-English notes |
| `app/scoring/weights.py` | Strategy weights + `MODEL_VERSION` |
| `app/scoring/engine.py` | Budget gate → deal math → factors → weighted score → program matches |
| `app/programs/seed_programs.json` | Program catalog (data, not code) |
| `app/programs/matcher.py` | Rules engine: eligible / likely / upcoming / not_eligible |
| `app/main.py` | FastAPI: `/health`, `/weights`, `/programs`, `/analyze`, `/analyze/address`, `/programs/match` |
| `app/data/` | API clients, parsers, database loaders, address enrichment, loader CLI |
| `tests/` | Sample Gulf Coast, Aberdeen and Columbus scenarios + tests |

## Design rules

- **Scores are deterministic and versioned.** Bump `MODEL_VERSION` when weights or formulas
  change; every stored analysis records the version that produced it. An LLM may explain
  the factor notes but never sets a score.
- **Programs are data.** Each has `source_url`, `last_verified`, and optional `start_date` /
  `end_date`; anything unverified for 90+ days is flagged. Programs in the same
  `stack_group` (MHC's DPA products, OZ variants) don't stack — only the largest counts.
- **Missing data means "likely", not "no".** MHC income/price limits and HUD AMI are passed
  via `reference` on the request until the database supplies them.
- **Fair housing.** No crime, school-rating or demographic-composition inputs in any score.

## Placeholders to replace before beta

- Insurance multipliers, 1.0% default tax rate and the MS median-income reference in
  `factors.py` / `deal_math.py` — replace with real quotes, assessor data and ACS.
- MHC county income and purchase-price limits (`agency_limits.mhc`).
- Mississippi's certified OZ 2.0 tract list (load into `tracts.oz_2_0` / `rural_flag`).
- Sample tracts in `tests/fixtures.py` are illustrative, not real data.

## Next up

1. Parcel data (Regrid or county GIS) for owner, assessed value, tax and zoning, then
   `/analyze` by parcel_id.
2. County scrapers for permits and rezonings, starting with Jackson and Lowndes.
3. Next.js front end with a MapLibre parcel map.

Estimates for research only; not financial, tax, legal or lending advice.
