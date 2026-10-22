-- Property Investment Analyzer — core schema (PostgreSQL 16 + PostGIS)
-- The census tract GEOID is the join key between parcels, demographics,
-- Opportunity Zone status, HUD data and program rules.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Geography
-- ---------------------------------------------------------------------------

CREATE TABLE tracts (
    tract_geoid        CHAR(11) PRIMARY KEY,          -- state(2)+county(3)+tract(6)
    county_fips        CHAR(5)  NOT NULL,
    geom               geometry(MultiPolygon, 4326),
    median_hh_income   INTEGER,
    population         INTEGER,
    population_5yr_pct NUMERIC(6,2),                  -- % change over 5 years (ACS)
    vacancy_rate       NUMERIC(5,2),                  -- % vacant housing units
    unemployment_rate  NUMERIC(5,2),
    oz_1_0             BOOLEAN NOT NULL DEFAULT FALSE, -- designated through 2028-12-31
    oz_2_0             BOOLEAN NOT NULL DEFAULT FALSE, -- designated 2027-01-01 .. 2036-12-31
    rural_flag         BOOLEAN NOT NULL DEFAULT FALSE, -- rural under OZ 2.0 definition
    usda_eligible      BOOLEAN,                        -- USDA RD rural eligibility (majority of tract)
    qct                BOOLEAN NOT NULL DEFAULT FALSE, -- HUD Qualified Census Tract
    dda                BOOLEAN NOT NULL DEFAULT FALSE, -- HUD Difficult Development Area
    acs_year           SMALLINT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX tracts_geom_idx   ON tracts USING GIST (geom);
CREATE INDEX tracts_county_idx ON tracts (county_fips);

CREATE TABLE parcels (
    parcel_id        TEXT PRIMARY KEY,                -- vendor id (e.g. Regrid ll_uuid)
    county_fips      CHAR(5) NOT NULL,
    apn              TEXT,                            -- county parcel number
    geom             geometry(MultiPolygon, 4326),
    centroid         geometry(Point, 4326),
    address          TEXT,
    city             TEXT,
    zip              CHAR(5),
    owner_name       TEXT,
    assessed_value   NUMERIC(14,2),
    annual_tax       NUMERIC(12,2),
    land_use_code    TEXT,
    zoning_code      TEXT,
    zoning_type      TEXT,                            -- residential / commercial / mixed / agricultural / industrial
    acres            NUMERIC(10,4),
    building_sqft    INTEGER,
    year_built       SMALLINT,
    tract_geoid      CHAR(11) REFERENCES tracts(tract_geoid),
    fema_zone        TEXT,                            -- denormalized from flood_zones on load
    source           TEXT NOT NULL DEFAULT 'regrid',
    source_updated   DATE,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX parcels_geom_idx     ON parcels USING GIST (geom);
CREATE INDEX parcels_centroid_idx ON parcels USING GIST (centroid);
CREATE INDEX parcels_tract_idx    ON parcels (tract_geoid);
CREATE INDEX parcels_address_idx  ON parcels (lower(address));

CREATE TABLE flood_zones (
    id              BIGSERIAL PRIMARY KEY,
    geom            geometry(MultiPolygon, 4326) NOT NULL,
    fema_zone       TEXT NOT NULL,                    -- A, AE, VE, X, X500 ...
    sfha            BOOLEAN NOT NULL,                 -- Special Flood Hazard Area
    effective_date  DATE
);
CREATE INDEX flood_zones_geom_idx ON flood_zones USING GIST (geom);

-- Rezonings, permits, new business filings, economic development projects.
CREATE TABLE area_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type    TEXT NOT NULL CHECK (event_type IN
                    ('permit','rezoning','new_business','ed_project','infrastructure')),
    geom          geometry(Point, 4326) NOT NULL,
    county_fips   CHAR(5),
    title         TEXT NOT NULL,
    details       JSONB NOT NULL DEFAULT '{}',        -- e.g. {"from_zone":"A-1","to_zone":"R-3","units":48}
    impact        TEXT CHECK (impact IN ('positive','neutral','negative')) DEFAULT 'neutral',
    event_date    DATE,
    status        TEXT,                               -- proposed / approved / under_construction / complete / denied
    source_url    TEXT,
    extracted_by  TEXT,                               -- scraper name or 'llm:<model>'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX area_events_geom_idx ON area_events USING GIST (geom);
CREATE INDEX area_events_date_idx ON area_events (event_date DESC);

-- ---------------------------------------------------------------------------
-- HUD reference data
-- ---------------------------------------------------------------------------

CREATE TABLE rent_benchmarks (
    area_code    TEXT NOT NULL,                       -- HUD FMR area code
    bedrooms     SMALLINT NOT NULL CHECK (bedrooms BETWEEN 0 AND 4),
    fmr          INTEGER NOT NULL,                    -- monthly $
    fiscal_year  SMALLINT NOT NULL,
    PRIMARY KEY (area_code, bedrooms, fiscal_year)
);

CREATE TABLE county_fmr_areas (
    county_fips  CHAR(5) NOT NULL,
    area_code    TEXT NOT NULL,
    fiscal_year  SMALLINT NOT NULL,
    PRIMARY KEY (county_fips, fiscal_year)
);

CREATE TABLE income_limits (
    area_code       TEXT NOT NULL,
    fiscal_year     SMALLINT NOT NULL,
    household_size  SMALLINT NOT NULL CHECK (household_size BETWEEN 1 AND 8),
    pct_ami         SMALLINT NOT NULL,                -- 30, 50, 60, 80, 100, 115
    amount          INTEGER NOT NULL,
    PRIMARY KEY (area_code, fiscal_year, household_size, pct_ami)
);

-- ---------------------------------------------------------------------------
-- Funding programs (data, not code)
-- ---------------------------------------------------------------------------

CREATE TABLE programs (
    id             TEXT PRIMARY KEY,                  -- slug, e.g. 'mhc-smart6'
    name           TEXT NOT NULL,
    agency         TEXT NOT NULL,
    category       TEXT NOT NULL CHECK (category IN ('homebuyer','investor','developer','tax')),
    benefit_text   TEXT NOT NULL,
    amount_max     INTEGER,
    stack_group    TEXT,                              -- programs in one group don't stack; count the largest
    start_date     DATE,
    end_date       DATE,                              -- NULL = open-ended
    source_url     TEXT NOT NULL,
    last_verified  DATE NOT NULL,
    active         BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE program_rules (
    id          BIGSERIAL PRIMARY KEY,
    program_id  TEXT NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    rule_type   TEXT NOT NULL,                        -- see app/programs/matcher.py RULE_TYPES
    params      JSONB NOT NULL DEFAULT '{}',
    description TEXT NOT NULL                         -- shown to the user when the rule fails
);
CREATE INDEX program_rules_program_idx ON program_rules (program_id);

-- ---------------------------------------------------------------------------
-- Users and analyses
-- ---------------------------------------------------------------------------

CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT UNIQUE NOT NULL,
    plan        TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free','investor','pro','team')),
    org_id      UUID,
    role        TEXT NOT NULL DEFAULT 'member',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE budget_profiles (
    user_id          UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    max_price        NUMERIC(14,2) NOT NULL,
    cash_available   NUMERIC(14,2) NOT NULL,
    target_coc_pct   NUMERIC(5,2) DEFAULT 8,
    financing        TEXT NOT NULL DEFAULT 'conventional',  -- cash / conventional / fha / va / usda / dscr
    household_size   SMALLINT,
    household_income INTEGER,
    first_time_buyer BOOLEAN,
    veteran          BOOLEAN,
    occupation       TEXT,
    has_capital_gains BOOLEAN,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE analyses (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID REFERENCES users(id) ON DELETE SET NULL,
    parcel_id        TEXT REFERENCES parcels(parcel_id),
    strategy         TEXT NOT NULL CHECK (strategy IN ('rental','commercial','personal','tax')),
    inputs           JSONB NOT NULL,                  -- full request, so results are reproducible
    factor_scores    JSONB NOT NULL,
    total_score      SMALLINT NOT NULL,
    deal_metrics     JSONB,
    program_matches  JSONB NOT NULL,
    model_version    TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX analyses_user_idx ON analyses (user_id, created_at DESC);

CREATE TABLE watchlists (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parcel_id    TEXT REFERENCES parcels(parcel_id),
    center       geometry(Point, 4326),
    radius_m     INTEGER DEFAULT 3000,
    event_types  TEXT[] NOT NULL DEFAULT ARRAY['rezoning','permit','new_business','ed_project'],
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (parcel_id IS NOT NULL OR center IS NOT NULL)
);

-- Phase 1 beta counties (Gulf Coast + Northeast MS)
CREATE TABLE coverage_counties (
    county_fips  CHAR(5) PRIMARY KEY,
    name         TEXT NOT NULL,
    region       TEXT NOT NULL,
    phase        SMALLINT NOT NULL
);
INSERT INTO coverage_counties VALUES
    ('28045','Hancock','Gulf Coast',1),
    ('28047','Harrison','Gulf Coast',1),
    ('28059','Jackson','Gulf Coast',1),
    ('28095','Monroe','Northeast MS',1),
    ('28087','Lowndes','Northeast MS',1),
    ('28105','Oktibbeha','Northeast MS',1),
    ('28025','Clay','Northeast MS',1);
