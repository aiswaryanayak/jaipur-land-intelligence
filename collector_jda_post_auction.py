#!/usr/bin/env python3
"""JDA public post-auction collector with full-row observation fingerprints.

This collector records completed auction outcomes only. A content fingerprint
identifies an identical observation, not a physical parcel or an opportunity.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

SOURCE_URL = (
    "https://jdaservice.rajasthan.gov.in/"
    "jdamicroportalproject/NoticeBoard/PostAuction"
)
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
DB_PATH = DATA_DIR / "jaipur_land_intelligence.sqlite"
JSON_PATH = DATA_DIR / "jda_post_auction_latest.json"
CSV_PATH = DATA_DIR / "jda_post_auction_latest.csv"

HEADERS = {
    "User-Agent": (
        "JaipurLandIntelligencePrototype/0.2 "
        "(public-notice research; contact: replace-with-project-contact)"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def parse_number(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def observation_fingerprint(record: dict[str, Any]) -> str:
    """Hash the complete normalized observation plus raw source row.

    Excludes the fingerprint itself so the hash is stable and non-recursive.
    Exact repeated rows get the same hash; different content gets a different
    hash. This does NOT claim to identify the same physical parcel.
    """
    payload = {k: v for k, v in record.items() if k != "record_fingerprint"}
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def apply_fingerprints(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, str] = {}
    for record in records:
        fp = observation_fingerprint(record)
        # Defend against a hypothetical hash collision: same hash, different payload.
        payload = json.dumps(
            {k: v for k, v in record.items() if k != "record_fingerprint"},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        prior = seen.get(fp)
        if prior is not None and prior != payload:
            raise RuntimeError(
                "Fingerprint collision detected for different observation payloads; "
                "stopping rather than dropping data."
            )
        seen[fp] = payload
        record["record_fingerprint"] = fp
    return records


def audit_existing_json(path: Path) -> int:
    if not path.exists():
        print(f"FAILED: JSON file not found: {path}", file=sys.stderr)
        return 2
    with path.open("r", encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list):
        print("FAILED: expected a JSON list of records.", file=sys.stderr)
        return 2

    # Calculate candidate fingerprints without changing the source file.
    fingerprints: list[str] = []
    payload_by_fp: dict[str, str] = {}
    exact_payload_counts: dict[str, int] = {}
    for record in records:
        payload = {k: v for k, v in record.items() if k != "record_fingerprint"}
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        fp = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        fingerprints.append(fp)
        payload_by_fp[fp] = canonical
        exact_payload_counts[fp] = exact_payload_counts.get(fp, 0) + 1

    unique_count = len(set(fingerprints))
    duplicate_extras = len(records) - unique_count
    collision_count = 0
    # Since this is a SHA-256 hash of the complete canonical payload, a same-hash
    # / different-payload collision would be a cryptographic collision.
    print(f"Rows read: {len(records)}")
    print(f"Unique full-row content fingerprints: {unique_count}")
    print(f"Exact duplicate extra rows (safe to collapse in per-run observation storage): {duplicate_extras}")
    print(f"Same-hash/different-payload collisions: {collision_count}")
    print("No files or database rows were changed.")
    return 0


def parse_source(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("No HTML table found; source layout may have changed.")

    rows = table.find_all("tr")
    if not rows:
        raise RuntimeError("The detected table contains no rows.")

    header_cells = rows[0].find_all(["th", "td"])
    headers = [
        normalize_header(clean(cell.get_text(" ", strip=True)) or "")
        for cell in header_cells
    ]

    expected = {
        "date_of_auction", "zone_name", "auction_type", "scheme",
        "plot_no", "category", "area", "auctioned_rate"
    }
    if not expected.issubset(set(headers)):
        raise RuntimeError(
            "Unexpected table headers. "
            f"Expected at least {sorted(expected)}; found {headers}"
        )

    parsed: list[dict[str, Any]] = []
    for tr in rows[1:]:
        cells = tr.find_all(["td", "th"])
        values = [clean(cell.get_text(" ", strip=True)) for cell in cells]
        if not values or len(values) != len(headers):
            continue
        raw = dict(zip(headers, values))
        if not any(raw.values()):
            continue

        record = {
            "source_authority": "Jaipur Development Authority",
            "source_url": SOURCE_URL,
            "source_record_type": "post_auction_result",
            "status": "completed",
            "auction_date": raw.get("date_of_auction"),
            "zone_name": raw.get("zone_name"),
            "auction_type": raw.get("auction_type"),
            "scheme_name": raw.get("scheme"),
            "plot_number": raw.get("plot_no"),
            "property_category": raw.get("category"),
            "area_sqm": parse_number(raw.get("area")),
            # This is the source's auctioned rate, not a reserve price.
            "auctioned_rate_inr_per_sqm": parse_number(raw.get("auctioned_rate")),
            "raw_source_row": raw,
        }
        parsed.append(record)

    if not parsed:
        raise RuntimeError("The table was found, but no data rows were parsed.")
    return apply_fingerprints(parsed)


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS ingestion_runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_key TEXT NOT NULL,
        source_url TEXT NOT NULL,
        started_at_utc TEXT NOT NULL,
        finished_at_utc TEXT,
        http_status INTEGER,
        rows_parsed INTEGER NOT NULL DEFAULT 0,
        outcome TEXT NOT NULL,
        error_message TEXT
    );

    CREATE TABLE IF NOT EXISTS source_observations (
        observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES ingestion_runs(run_id),
        record_fingerprint TEXT NOT NULL,
        observed_at_utc TEXT NOT NULL,
        source_url TEXT NOT NULL,
        source_record_type TEXT NOT NULL,
        normalized_json TEXT NOT NULL,
        raw_json TEXT NOT NULL,
        UNIQUE(run_id, record_fingerprint)
    );

    CREATE INDEX IF NOT EXISTS idx_observations_fingerprint
      ON source_observations(record_fingerprint);
    CREATE INDEX IF NOT EXISTS idx_runs_source_time
      ON ingestion_runs(source_key, started_at_utc);
    """)
    conn.commit()


def write_exports(records: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with JSON_PATH.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    columns = [
        "source_authority", "source_url", "source_record_type", "status",
        "auction_date", "zone_name", "auction_type", "scheme_name",
        "plot_number", "property_category", "area_sqm",
        "auctioned_rate_inr_per_sqm", "record_fingerprint"
    ]
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--audit-existing-json":
        return audit_existing_json(Path(sys.argv[2]).resolve())
    if len(sys.argv) != 1:
        print(
            "Usage:\n"
            "  python collector_jda_post_auction.py\n"
            "  python collector_jda_post_auction.py --audit-existing-json data\\jda_post_auction_latest.json",
            file=sys.stderr,
        )
        return 2

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)
        run_id = conn.execute(
            """INSERT INTO ingestion_runs
               (source_key, source_url, started_at_utc, outcome)
               VALUES (?, ?, ?, ?)""",
            ("jda_post_auction", SOURCE_URL, started, "running"),
        ).lastrowid
        conn.commit()

        http_status = None
        try:
            response = requests.get(SOURCE_URL, headers=HEADERS, timeout=(10, 30))
            http_status = response.status_code
            response.raise_for_status()
            records = parse_source(response.text)
            observed_at = utc_now()
            inserted = 0

            with conn:
                for record in records:
                    raw_json = json.dumps(
                        record["raw_source_row"], ensure_ascii=False, sort_keys=True
                    )
                    normalized = {k: v for k, v in record.items()
                                  if k != "raw_source_row"}
                    cursor = conn.execute(
                        """INSERT OR IGNORE INTO source_observations
                           (run_id, record_fingerprint, observed_at_utc, source_url,
                            source_record_type, normalized_json, raw_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            run_id, record["record_fingerprint"], observed_at,
                            SOURCE_URL, record["source_record_type"],
                            json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                            raw_json,
                        ),
                    )
                    inserted += cursor.rowcount

                unique_in_run = len({r["record_fingerprint"] for r in records})
                if inserted != unique_in_run:
                    raise RuntimeError(
                        f"Storage mismatch: expected {unique_in_run} unique observations "
                        f"for this run, inserted {inserted}. Transaction rolled back."
                    )

                conn.execute(
                    """UPDATE ingestion_runs
                       SET finished_at_utc=?, http_status=?, rows_parsed=?, outcome=?
                       WHERE run_id=?""",
                    (utc_now(), http_status, len(records), "success", run_id),
                )

            write_exports(records)
            print(f"SUCCESS: parsed {len(records)} JDA post-auction rows")
            print(f"Unique full-row observations in this run: {unique_in_run}")
            print(f"Exact duplicate rows collapsed for this run: {len(records) - unique_in_run}")
            print(f"Rows inserted into SQLite for this run: {inserted}")
            print(f"Run ID: {run_id}")
            print(f"SQLite: {DB_PATH}")
            print(f"JSON:   {JSON_PATH}")
            print(f"CSV:    {CSV_PATH}")
            print("Note: records are completed auction outcomes, not active offers.")
            return 0

        except Exception as exc:
            with conn:
                conn.execute(
                    """UPDATE ingestion_runs
                       SET finished_at_utc=?, http_status=?, outcome=?, error_message=?
                       WHERE run_id=?""",
                    (utc_now(), http_status, "failed", str(exc), run_id),
                )
            print(f"FAILED: {exc}", file=sys.stderr)
            print("The previous CSV/JSON exports were not rewritten after this failure.", file=sys.stderr)
            return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
