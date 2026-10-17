#!/usr/bin/env python3
"""Create the source audit and isolated staging dataset for authority expansion."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "authority_staging"

SOURCES = [
    {
        "authority": "JDA / UDH Online",
        "source_type": "auction catalogue",
        "source_url": "https://www.udhonline.rajasthan.gov.in/",
        "access_status": "transport_failure_during_audit",
        "staging_policy": "Existing 238 canonical JDA records remain in the production catalogue; no new rows staged.",
        "notes": "Root URL failed with a transport-level DNS/TLS error during this audit. Existing UDH catalogue URLs in the local source records are retained.",
    },
    {
        "authority": "RIICO",
        "source_type": "e-auctions",
        "source_url": "https://www.riicoerp.industries.rajasthan.gov.in/eauction",
        "access_status": "reachable_page_requires_structured_record_review",
        "staging_policy": "No records staged until official row identifiers, Jaipur-region scope, and status fields are extracted and reviewed.",
        "notes": "Official page was reachable, but page availability alone does not support importing records or inferring missing fields.",
    },
    {
        "authority": "RIICO",
        "source_type": "direct land allotment",
        "source_url": "https://riico.rajasthan.gov.in/DLA.aspx",
        "access_status": "reachable_page_requires_structured_record_review",
        "staging_policy": "Keep direct allotments separate from auctions; no records staged until identifiers and application dates are extracted.",
        "notes": "Official direct-allotment page was reachable, but no records were imported from a page audit alone.",
    },
]

FIELDS = [
    "record_key",
    "authority",
    "source_type",
    "source_url",
    "original_identifier",
    "title",
    "property_or_plot_number",
    "scheme_or_industrial_area",
    "land_use_category",
    "area",
    "bid_start",
    "reserve_price_or_rate",
    "emd",
    "application_start",
    "application_end",
    "bid_end",
    "current_status",
    "last_checked_at",
    "dedupe_key",
    "review_status",
    "notes",
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    staging_path = OUT / "opportunity_staging.json"
    existing_records = []
    if staging_path.exists():
        existing = json.loads(staging_path.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and isinstance(existing.get("records"), list):
            existing_records = existing["records"]
    audit_path = OUT / "source_audit.json"
    existing_audit = {}
    if audit_path.exists():
        existing_audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit = {
        "generated_at": stamp,
        "production_catalogue_untouched": True,
        "staging_record_count": len(existing_records),
        "new_count": 0,
        "duplicate_count": 0,
        "rejected_count": 0,
        "unresolved_count": 0,
        "sources": existing_audit.get("sources", SOURCES),
        "staging_fields": FIELDS,
        "status_policy": {
            "active": "Only an official source explicitly identifies the opportunity as active.",
            "upcoming": "Official dates are future, but current availability still requires verification.",
            "closed": "Official closing/bid date has passed; never label active.",
            "historical": "Retained for provenance only and not presented as an active opportunity.",
        },
    }
    for key, value in existing_audit.items():
        if key not in {"generated_at", "production_catalogue_untouched",
                       "staging_record_count", "new_count"}:
            audit[key] = value
    audit_path.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    staging_csv = OUT / "opportunity_staging.csv"
    if not staging_csv.exists():
        with staging_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.DictWriter(handle, fieldnames=FIELDS).writeheader()
    if not staging_path.exists():
        staging_path.write_text(
            json.dumps({"generated_at": stamp, "records": []}, indent=2),
            encoding="utf-8",
        )
    print("Sources audited:", len(SOURCES))
    print(f"Staged records preserved: {len(existing_records)}")
    print("New: 0; duplicate: 0; rejected: 0; unresolved: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
