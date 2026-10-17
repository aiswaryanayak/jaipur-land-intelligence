#!/usr/bin/env python3
"""Create/update a local SQLite database from deploy_data/auctions.json.

Run from the project folder:
    python init_land_intelligence_db.py
Optional paths:
    python init_land_intelligence_db.py --source deploy_data/auctions.json --db data/jaipur_land_intelligence.sqlite3

All source rows (including duplicates) are preserved. One canonical record per
auction_id is kept in auctions. Scheme labels are not merged unless they differ
only by case/whitespace. Manually entered locations survive repeat imports.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "deploy_data" / "auctions.json"
DEFAULT_DB = ROOT / "data" / "jaipur_land_intelligence.sqlite3"

SCHEMA = '''
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS scheme_labels (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 label TEXT NOT NULL,
 normalized_label TEXT NOT NULL UNIQUE,
 identity_status TEXT NOT NULL DEFAULT 'unverified_label',
 first_seen_at TEXT NOT NULL,
 last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auctions (
 auction_id TEXT PRIMARY KEY, title TEXT, scheme_name TEXT, property_number TEXT,
 property_area TEXT, usage_type TEXT, assessed_value TEXT, bid_start TEXT,
 bid_end TEXT, emd_start TEXT, emd_end TEXT, emd_amount TEXT,
 source_authority TEXT, source_url TEXT, retrieved_at_utc TEXT,
 record_json TEXT NOT NULL, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auction_scheme_labels (
 auction_id TEXT NOT NULL, scheme_label_id INTEGER NOT NULL,
 PRIMARY KEY (auction_id, scheme_label_id),
 FOREIGN KEY (auction_id) REFERENCES auctions(auction_id) ON DELETE CASCADE,
 FOREIGN KEY (scheme_label_id) REFERENCES scheme_labels(id)
);
CREATE TABLE IF NOT EXISTS auction_source_records (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_row_number INTEGER NOT NULL UNIQUE,
 auction_id TEXT, scheme_label TEXT, row_sha256 TEXT NOT NULL,
 record_json TEXT NOT NULL, imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auctions_scheme_name ON auctions(scheme_name);
CREATE INDEX IF NOT EXISTS idx_source_records_auction_id ON auction_source_records(auction_id);
CREATE INDEX IF NOT EXISTS idx_source_records_scheme_label ON auction_source_records(scheme_label);
CREATE TABLE IF NOT EXISTS locations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scheme_label_id INTEGER NOT NULL,
 latitude REAL NOT NULL, longitude REAL NOT NULL, precision TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'needs_verification', source_url TEXT,
 evidence_notes TEXT, verified_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 coordinate_subject TEXT, verification_status TEXT, confidence TEXT,
 FOREIGN KEY (scheme_label_id) REFERENCES scheme_labels(id),
 CHECK (latitude BETWEEN -90 AND 90), CHECK (longitude BETWEEN -180 AND 180)
);
CREATE INDEX IF NOT EXISTS idx_locations_scheme_label_id ON locations(scheme_label_id);
'''

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def clean(value: Any) -> str:
    return " ".join(str(value).strip().split()) if value is not None else ""

def label_for(row: dict[str, Any]) -> str:
    return clean(row.get("scheme_name") or row.get("catalog_scheme_name"))

def aid_for(row: dict[str, Any]) -> str:
    return clean(row.get("auction_id"))

def fingerprint(row: dict[str, Any]) -> str:
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def completeness(row: dict[str, Any]) -> int:
    return sum(v not in (None, "", [], {}) for v in row.values())

def load_rows(source: Path) -> list[dict[str, Any]]:
    with source.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = next((data[k] for k in ("auctions", "records", "data", "items") if isinstance(data.get(k), list)), None)
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError("Expected a JSON list of records (or a wrapper with auctions/records/data/items list).")
    return data

def migrate(source: Path, db_path: Path, rows: list[dict[str, Any]]) -> dict[str, int]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = now_utc()
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(SCHEMA)
        with con:
            # Refresh current catalogue snapshot but keep scheme labels and location evidence.
            con.execute("DELETE FROM auction_source_records")
            con.execute("DELETE FROM auction_scheme_labels")
            con.execute("DELETE FROM auctions")

            for row in rows:
                label = label_for(row)
                if not label:
                    continue
                normalized = label.casefold()  # deliberately conservative; no punctuation merging
                con.execute('''INSERT INTO scheme_labels(label, normalized_label, identity_status, first_seen_at, last_seen_at)
                               VALUES (?, ?, 'unverified_label', ?, ?)
                               ON CONFLICT(normalized_label) DO UPDATE SET last_seen_at=excluded.last_seen_at''',
                            (label, normalized, stamp, stamp))

            canonical: dict[str, dict[str, Any]] = {}
            for row in rows:
                aid = aid_for(row)
                if aid and (aid not in canonical or completeness(row) > completeness(canonical[aid])):
                    canonical[aid] = row

            for aid, row in canonical.items():
                vals = [clean(row.get(k)) or None for k in (
                    "title", "scheme_name", "property_number", "property_area", "usage_type",
                    "assessed_value", "bid_start", "bid_end", "emd_start", "emd_end", "emd_amount",
                    "source_authority", "source_url", "retrieved_at_utc")]
                con.execute('''INSERT INTO auctions(auction_id,title,scheme_name,property_number,property_area,usage_type,
                               assessed_value,bid_start,bid_end,emd_start,emd_end,emd_amount,source_authority,source_url,
                               retrieved_at_utc,record_json,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            [aid, *vals, json.dumps(row, ensure_ascii=False, sort_keys=True), stamp])

            label_ids = {r["normalized_label"]: r["id"] for r in con.execute("SELECT id, normalized_label FROM scheme_labels")}
            for n, row in enumerate(rows, start=1):
                aid = aid_for(row) or None
                label = label_for(row) or None
                con.execute('''INSERT INTO auction_source_records(source_row_number,auction_id,scheme_label,row_sha256,record_json,imported_at)
                               VALUES(?,?,?,?,?,?)''',
                            (n, aid, label, fingerprint(row), json.dumps(row, ensure_ascii=False, sort_keys=True), stamp))
                if aid and label:
                    label_id = label_ids.get(label.casefold())
                    if label_id is not None:
                        con.execute("INSERT OR IGNORE INTO auction_scheme_labels(auction_id,scheme_label_id) VALUES(?,?)", (aid, label_id))

        return {
            "source_rows": con.execute("SELECT COUNT(*) FROM auction_source_records").fetchone()[0],
            "unique_auctions": con.execute("SELECT COUNT(*) FROM auctions").fetchone()[0],
            "scheme_labels": con.execute("SELECT COUNT(*) FROM scheme_labels").fetchone()[0],
            "location_records_preserved": con.execute("SELECT COUNT(*) FROM locations").fetchone()[0],
        }
    finally:
        con.close()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    source, db_path = args.source.expanduser().resolve(), args.db.expanduser().resolve()
    if not source.is_file():
        print(f"ERROR: source JSON not found: {source}\nPass --source <path-to-auctions.json> if needed.", file=sys.stderr)
        return 2
    try:
        rows = load_rows(source)
        counts = migrate(source, db_path, rows)
    except (OSError, json.JSONDecodeError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("LOCAL DATABASE INITIALIZED")
    print(f"Source: {source}")
    print(f"Database: {db_path}")
    print(f"Input rows preserved: {counts['source_rows']}")
    print(f"Unique auction IDs: {counts['unique_auctions']}")
    print(f"Observed scheme labels (not confirmed unique physical schemes): {counts['scheme_labels']}")
    print(f"Existing location records preserved: {counts['location_records_preserved']}")
    print("Safe to rerun: imports refresh source rows without multiplying them.")
    print("No app.py, Git, GitHub, or live deployment was changed.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
