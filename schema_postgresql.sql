-- Proposed PostgreSQL schema for the next iteration.
-- SQLite is used by the first local prototype to avoid setup friction.

CREATE TABLE authorities (
    authority_id BIGSERIAL PRIMARY KEY,
    authority_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    jurisdiction_notes TEXT,
    official_homepage TEXT
);

CREATE TABLE sources (
    source_id BIGSERIAL PRIMARY KEY,
    authority_id BIGINT REFERENCES authorities(authority_id),
    source_key TEXT NOT NULL UNIQUE,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    record_type TEXT NOT NULL,
    access_review_status TEXT NOT NULL DEFAULT 'needs_review',
    collection_method TEXT,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    last_error TEXT
);

CREATE TABLE opportunities (
    opportunity_id BIGSERIAL PRIMARY KEY,
    authority_id BIGINT REFERENCES authorities(authority_id),
    canonical_key TEXT UNIQUE,
    opportunity_type TEXT NOT NULL,
    scheme_name TEXT,
    property_identifier TEXT,
    locality TEXT,
    property_category TEXT,
    area_value NUMERIC,
    area_unit TEXT,
    reserve_price_inr NUMERIC,
    auctioned_rate_inr_per_unit NUMERIC,
    emd_inr NUMERIC,
    publication_date DATE,
    application_deadline TIMESTAMPTZ,
    bid_start_at TIMESTAMPTZ,
    bid_end_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'unknown',
    verification_status TEXT NOT NULL DEFAULT 'unreviewed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE source_observations (
    observation_id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(source_id),
    opportunity_id BIGINT REFERENCES opportunities(opportunity_id),
    source_record_key TEXT,
    source_url TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content_sha256 TEXT,
    raw_payload JSONB NOT NULL,
    extracted_payload JSONB,
    parse_status TEXT NOT NULL DEFAULT 'parsed',
    UNIQUE(source_id, source_record_key, content_sha256)
);

CREATE TABLE source_documents (
    document_id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(source_id),
    opportunity_id BIGINT REFERENCES opportunities(opportunity_id),
    document_url TEXT NOT NULL,
    document_title TEXT,
    published_at DATE,
    downloaded_at TIMESTAMPTZ,
    content_sha256 TEXT,
    extraction_status TEXT NOT NULL DEFAULT 'not_processed'
);

CREATE TABLE opportunity_events (
    event_id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT NOT NULL REFERENCES opportunities(opportunity_id),
    source_observation_id BIGINT REFERENCES source_observations(observation_id),
    event_type TEXT NOT NULL,
    event_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    details JSONB
);

CREATE TABLE ingestion_runs (
    run_id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES sources(source_id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    outcome TEXT NOT NULL DEFAULT 'running',
    http_status INTEGER,
    rows_parsed INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);
