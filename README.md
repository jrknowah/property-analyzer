# Property Investment Analyzer — backend core

Scores a property for a chosen strategy (rental, commercial, personal, tax) against a
buyer's budget, runs the deal math, and matches funding programs (MHC, USDA, FHA/VA,
Opportunity Zones 1.0/2.0, LIHTC QCT/DDA). Phase 1 covers the Gulf Coast (Hancock,
Harrison, Jackson) and Northeast Mississippi (Monroe, Lowndes, Oktibbeha, Clay).

## Run it

```bash
pip install -r requirements.txt
pytest -q                                  # 15 tests
uvicorn app.main:app --reload              # http://localhost:8000/docs
curl -s localhost:8000/analyze -H 'content-type: application/json' \
     -d @examples/first_time_buyer_columbus.json | jq '.total_score, .grade, .programs[0]'
```

With Docker (API + PostGIS, schema applied on first start): `docker compose up`.

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
| `app/main.py` | FastAPI: `/health`, `/weights`, `/programs`, `/analyze`, `/programs/match` |
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

1. Loaders: Census geocoder + ACS, HUD FMR/income-limit API, FEMA NFHL, OZ tract files.
2. `GET /parcels/{id}` and `/analyze` by parcel_id once parcels are loaded.
3. County scrapers for permits and rezonings, starting with Jackson and Lowndes.
4. Next.js front end with a MapLibre parcel map.

Estimates for research only; not financial, tax, legal or lending advice.
