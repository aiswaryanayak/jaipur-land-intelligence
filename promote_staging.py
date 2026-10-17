#!/usr/bin/env python3
"""Preview or explicitly promote reviewed opportunity staging into SQLite.

The default operation is a read-only dry run. Promotion requires both
--apply and --confirm-production-promotion, and creates a database backup
before changing rows. Unreviewed, incomplete, duplicate, and conflicting
records are never promoted.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "jaipur_land_intelligence.sqlite3"
DEFAULT_INPUTS = [
    ROOT / "data" / "authority_staging" / "opportunity_staging.json",
    ROOT / "data" / "authority_staging" / "new_opportunity_discoveries.json",
]
APPROVED_REVIEW_STATUSES = {"approved_for_catalogue"}
REQUIRED = {
    "authority", "source_type", "source_url", "original_identifier", "title",
    "property_or_plot_number", "scheme_or_industrial_area", "area",
    "reserve_price_or_rate", "emd", "application_start", "application_end",
    "bid_start", "bid_end", "current_status", "last_checked_at",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            batch = payload.get("records")
        else:
            batch = payload
        if not isinstance(batch, list) or not all(isinstance(row, dict) for row in batch):
            raise ValueError(f"{path} does not contain a records list")
        records.extend(batch)
    return records


def row_key(row: dict[str, Any]) -> str:
    return str(row.get("original_identifier") or row.get("record_key") or "")


def validate_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    valid: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in records:
        key = row_key(row)
        missing = sorted(field for field in REQUIRED if not str(row.get(field) or "").strip())
        if not key:
            skipped.append({"key": "", "reason": "missing stable source identifier"})
        elif key in seen:
            skipped.append({"key": key, "reason": "duplicate within promotion inputs"})
        elif missing:
            skipped.append({"key": key, "reason": "missing fields: " + ", ".join(missing)})
        elif row.get("review_status") not in APPROVED_REVIEW_STATUSES:
            skipped.append({
                "key": key,
                "reason": f"review_status {row.get('review_status')!r} is not approved_for_catalogue",
            })
        else:
            valid.append(row)
            seen.add(key)
    return valid, skipped


def source_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "authority": row["authority"],
        "source_type": row["source_type"],
        "source_identifier": row["original_identifier"],
        "record_key": row.get("record_key"),
        "title": row["title"],
        "plot": row["property_or_plot_number"],
        "industrial_area_or_scheme": row["scheme_or_industrial_area"],
        "land_use": row["land_use_category"],
        "area": row["area"],
        "reserve_or_bid_rate": row["reserve_price_or_rate"],
        "emd": row["emd"],
        "application_start": row["application_start"],
        "application_end": row["application_end"],
        "bid_start": row["bid_start"],
        "bid_end": row["bid_end"],
        "portal_status": row["current_status"],
        "availability_status": row["current_status"],
        "last_checked_at": row["last_checked_at"],
        "review_status": row["review_status"],
        "notes": row.get("notes", ""),
    }


def preview(records: list[dict[str, Any]], db_path: Path) -> dict[str, Any]:
    valid, skipped = validate_records(records)
    additions: list[str] = []
    updates: list[str] = []
    conflicts: list[dict[str, str]] = []
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        for row in valid:
            key = row_key(row)
            existing = db.execute(
                "SELECT source_authority, record_json FROM auctions WHERE auction_id = ?",
                (key,),
            ).fetchone()
            if existing is None:
                additions.append(key)
                continue
            if existing[0] and existing[0].casefold() != row["authority"].casefold():
                conflicts.append({
                    "key": key,
                    "reason": f"existing authority {existing[0]!r} differs from {row['authority']!r}",
                })
                continue
            current = json.loads(existing[1])
            if current != source_record(row):
                updates.append(key)
    return {
        "input_count": len(records),
        "validated_count": len(valid),
        "skipped": skipped,
        "conflicts": conflicts,
        "additions": additions,
        "updates": updates,
        "historical_or_closed_not_available": [
            row_key(row) for row in valid
            if "closed" in str(row.get("current_status", "")).casefold()
            or "historical" in str(row.get("current_status", "")).casefold()
        ],
        "staged_closed_or_historical": [
            row_key(row) for row in records
            if "closed" in str(row.get("current_status", "")).casefold()
            or "historical" in str(row.get("current_status", "")).casefold()
        ],
    }


def backup_database(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=False)
    destination = backup_dir / db_path.name
    shutil.copy2(db_path, destination)
    return destination


def apply_records(records: list[dict[str, Any]], db_path: Path) -> dict[str, int]:
    valid, skipped = validate_records(records)
    if skipped:
        raise ValueError("Refusing promotion while any input record is not approved and valid")
    stamp = now_utc()
    added = updated = 0
    with sqlite3.connect(str(db_path)) as db:
        db.execute("PRAGMA foreign_keys = ON")
        with db:
            for row in valid:
                key = row_key(row)
                label = row["scheme_or_industrial_area"]
                normalized = " ".join(label.casefold().split())
                db.execute(
                    """INSERT INTO scheme_labels(label, normalized_label, identity_status, first_seen_at, last_seen_at)
                    VALUES (?, ?, 'unverified_label', ?, ?)
                    ON CONFLICT(normalized_label) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                    (label, normalized, stamp, stamp),
                )
                record = source_record(row)
                values = [
                    key, row["title"], label, row["property_or_plot_number"],
                    row["area"], row["land_use_category"], row["reserve_price_or_rate"],
                    row["bid_start"], row["bid_end"], row["application_start"],
                    row["application_end"], row["emd"], row["authority"],
                    row["source_url"], row["last_checked_at"],
                    json.dumps(record, ensure_ascii=False, sort_keys=True), stamp,
                ]
                existing = db.execute(
                    "SELECT 1 FROM auctions WHERE auction_id = ?", (key,)
                ).fetchone()
                if existing:
                    db.execute(
                        """UPDATE auctions SET title=?, scheme_name=?, property_number=?,
                        property_area=?, usage_type=?, assessed_value=?, bid_start=?,
                        bid_end=?, emd_start=?, emd_end=?, emd_amount=?,
                        source_authority=?, source_url=?, retrieved_at_utc=?,
                        record_json=?, imported_at=? WHERE auction_id=?""",
                        values[1:] + [key],
                    )
                    updated += 1
                else:
                    db.execute(
                        """INSERT INTO auctions(auction_id,title,scheme_name,property_number,
                        property_area,usage_type,assessed_value,bid_start,bid_end,emd_start,
                        emd_end,emd_amount,source_authority,source_url,retrieved_at_utc,
                        record_json,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        values,
                    )
                    added += 1
    return {"added": added, "updated": updated}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, dest="inputs")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production-promotion", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    inputs = [path.expanduser() for path in (args.inputs or DEFAULT_INPUTS)]
    records = load_records(inputs)
    result = preview(records, args.db.expanduser())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not args.apply:
        print("DRY RUN: no database changes made.")
        return 0
    if not args.confirm_production_promotion:
        print("REFUSED: --apply requires --confirm-production-promotion.")
        return 2
    if result["skipped"] or result["conflicts"]:
        print("REFUSED: validation skips or authority conflicts require review.")
        return 2
    backup_dir = args.backup_dir or args.db.parent / f"backup_promotion_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_database(args.db.expanduser(), backup_dir)
    print(json.dumps(apply_records(records, args.db.expanduser()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
