#!/usr/bin/env python3
"""Import explicitly labelled locality candidates without upgrading verification."""
from __future__ import annotations

import csv
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "jaipur_land_intelligence.sqlite3"
CSV_PATH = ROOT / "jli_researched_location_candidates.csv"
IMPORT_LABELS = {
    "deendayal nagar",
    "hari enclave",
    "gokul nagar yojana (kalwar road)",
}


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> int:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    candidates = [row for row in rows if normalize(row["scheme_label"]) in IMPORT_LABELS]
    if len(candidates) != len(IMPORT_LABELS):
        raise ValueError("Expected exactly one candidate for each approved label.")
    if any(
        not row["latitude"]
        or not row["longitude"]
        or row["verification_status"] != "candidate_not_scheme_verified"
        or row["precision"] != "locality_only"
        for row in candidates
    ):
        raise ValueError("Candidate metadata does not meet the locality-only import policy.")

    stamp = now()
    with sqlite3.connect(DB) as con:
        con.execute("PRAGMA foreign_keys = ON")
        existing_columns = {
            row[1] for row in con.execute("PRAGMA table_info(locations)")
        }
        for name, definition in (
            ("coordinate_subject", "TEXT"),
            ("verification_status", "TEXT"),
            ("confidence", "TEXT"),
        ):
            if name not in existing_columns:
                con.execute(f"ALTER TABLE locations ADD COLUMN {name} {definition}")

        imported = 0
        skipped = []
        for row in candidates:
            label = " ".join(row["scheme_label"].split())
            normalized = normalize(label)
            label_row = con.execute(
                "SELECT id, label FROM scheme_labels WHERE normalized_label = ?",
                (normalized,),
            ).fetchone()
            if label_row is None:
                con.execute(
                    """
                    INSERT INTO scheme_labels
                        (label, normalized_label, identity_status, first_seen_at, last_seen_at)
                    VALUES (?, ?, 'official_research_label', ?, ?)
                    """,
                    (label, normalized, stamp, stamp),
                )
                label_id = con.execute(
                    "SELECT id FROM scheme_labels WHERE normalized_label = ?",
                    (normalized,),
                ).fetchone()[0]
            else:
                label_id = label_row[0]

            duplicate = con.execute(
                """
                SELECT id FROM locations
                WHERE scheme_label_id = ? AND latitude = ? AND longitude = ?
                """,
                (label_id, float(row["latitude"]), float(row["longitude"])),
            ).fetchone()
            if duplicate is not None:
                skipped.append(label)
                continue

            con.execute(
                """
                INSERT INTO locations (
                    scheme_label_id, latitude, longitude, precision, status,
                    source_url, evidence_notes, verified_at, created_at, updated_at,
                    coordinate_subject, verification_status, confidence
                ) VALUES (?, ?, ?, ?, 'provisional', ?, ?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    label_id,
                    float(row["latitude"]),
                    float(row["longitude"]),
                    row["precision"],
                    row["coordinate_source_url"],
                    (
                        f"{row['notes']} Official source: {row['official_source_url']} "
                        "This is locality navigation only; the actual scheme boundary "
                        "and plot are unverified."
                    ),
                    stamp,
                    stamp,
                    row["coordinate_subject"],
                    row["verification_status"],
                    row["confidence"],
                ),
            )
            imported += 1
        con.commit()

        print(f"Candidates read: {len(candidates)}")
        print(f"Imported locality candidates: {imported}")
        print(f"Skipped existing candidates: {len(skipped)}")
        if skipped:
            print("Skipped labels: " + ", ".join(skipped))
        print(f"Locations total: {con.execute('SELECT COUNT(*) FROM locations').fetchone()[0]}")
        print(f"Auction records total: {con.execute('SELECT COUNT(*) FROM auctions').fetchone()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
