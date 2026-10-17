#!/usr/bin/env python3
"""Seed the existing reviewed scheme-area pins into the normalized SQLite table.

This migration only inserts exact scheme-label matches and never changes auction
records or creates coordinates from catalogue data.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "jaipur_land_intelligence.sqlite3"

REVIEWED_LOCATIONS = (
    {
        "label": "YAMUNA VIHAR",
        "latitude": 26.6606513,
        "longitude": 75.9191534,
        "precision": "approximate scheme-area",
        "status": "provisional",
        "source_url": "https://jdafa.rajasthan.gov.in/Confluence/Home/Lot/22",
        "evidence_notes": (
            "Existing reviewed scheme-area pin from the candidate map registry. "
            "It is not an exact plot, entrance, or legal-boundary coordinate."
        ),
    },
    {
        "label": "RIYASAT NIRVANA",
        "latitude": 26.6656,
        "longitude": 75.9275,
        "precision": "approximate scheme-area",
        "status": "provisional",
        "source_url": (
            "https://rera.rajasthan.gov.in/app/Content/ProjectCommanarea/"
            "Drawings/2167b0e6-f73d-49ab-a278-24e30735be75.pdf"
        ),
        "evidence_notes": (
            "Existing reviewed candidate based on OCR village/khasra evidence "
            "and a matching RERA filing. Verify against the official site plan; "
            "this is not an exact plot coordinate."
        ),
    },
)


def main() -> int:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for location in REVIEWED_LOCATIONS:
            normalized = " ".join(location["label"].split()).casefold()
            scheme = connection.execute(
                "SELECT id FROM scheme_labels WHERE normalized_label = ?",
                (normalized,),
            ).fetchone()
            if scheme is None:
                raise ValueError(
                    f"Exact scheme label not found; migration cancelled: {location['label']}"
                )

            existing = connection.execute(
                """
                SELECT id FROM locations
                WHERE scheme_label_id = ? AND latitude = ? AND longitude = ?
                """,
                (scheme[0], location["latitude"], location["longitude"]),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO locations (
                        scheme_label_id, latitude, longitude, precision, status,
                        source_url, evidence_notes, verified_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        scheme[0], location["latitude"], location["longitude"],
                        location["precision"], location["status"],
                        location["source_url"], location["evidence_notes"], now, now,
                    ),
                )
        connection.commit()

    print(f"Reviewed scheme locations present: {len(REVIEWED_LOCATIONS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
