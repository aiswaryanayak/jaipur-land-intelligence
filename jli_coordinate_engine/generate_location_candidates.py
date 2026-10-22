#!/usr/bin/env python3
"""Create a reviewable location-evidence queue from Jaipur Land Intelligence data.

This intentionally does NOT invent latitude/longitude from plot numbers or scheme names.
It produces one row per auction_id so official plans/GIS evidence can be matched and
coordinates can be added only after validation.
"""
from __future__ import annotations
import argparse
import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse

FIELDS = [
    "auction_id", "title", "scheme_name", "property_number", "property_area",
    "usage_type", "source_authority", "source_url", "report_url", "detail_links",
    "location_level", "latitude", "longitude", "verification_status", "method",
    "evidence_url", "estimated_error_m", "review_notes"
]


def clean(value):
    return "" if value is None else str(value).strip()


def official_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host == "udhonline.rajasthan.gov.in" or host == "jda.rajasthan.gov.in"
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="deploy_data/auctions.json", help="Catalogue JSON path")
    parser.add_argument("--out", default="data/location_research", help="Output directory")
    args = parser.parse_args()
    src = Path(args.input)
    out = Path(args.out)
    if not src.exists():
        raise SystemExit(f"Input not found: {src}. Run this from the project root or pass --input.")
    payload = json.loads(src.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise SystemExit("Expected a JSON object containing a 'records' list, or a list of records.")

    # Keep one review row per auction ID, preserving the first usable record and merging links.
    unique = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        aid = clean(rec.get("auction_id"))
        if not aid:
            continue
        if aid not in unique:
            unique[aid] = dict(rec)
            unique[aid]["_links"] = []
        target = unique[aid]
        for key in ("source_url", "report_url"):
            value = clean(rec.get(key))
            if value and value not in target["_links"]:
                target["_links"].append(value)
        links = rec.get("detail_links", [])
        if isinstance(links, str):
            links = [links]
        if isinstance(links, list):
            for value in links:
                value = clean(value)
                if value and value not in target["_links"]:
                    target["_links"].append(value)

    rows = []
    for aid, rec in sorted(unique.items(), key=lambda kv: kv[0]):
        links = rec.get("_links", [])
        official_links = [u for u in links if official_url(u)]
        rows.append({
            "auction_id": aid,
            "title": clean(rec.get("title")),
            "scheme_name": clean(rec.get("scheme_name") or rec.get("catalog_scheme_name")),
            "property_number": clean(rec.get("property_number")),
            "property_area": clean(rec.get("property_area")),
            "usage_type": clean(rec.get("usage_type")),
            "source_authority": clean(rec.get("source_authority")),
            "source_url": clean(rec.get("source_url")),
            "report_url": clean(rec.get("report_url")),
            "detail_links": json.dumps(official_links, ensure_ascii=False),
            "location_level": "unknown",
            "latitude": "",
            "longitude": "",
            "verification_status": "pending",
            "method": "",
            "evidence_url": "",
            "estimated_error_m": "",
            "review_notes": "Find official location/site plan or authoritative GIS data; do not infer exact coordinates from name/plot number."
        })

    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "location_candidates.csv"
    json_path = out / "location_candidates.json"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps({
        "generated_from": str(src),
        "input_observation_count": len(records),
        "unique_auction_count": len(rows),
        "coordinate_policy": "No coordinates generated without location evidence.",
        "records": rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Input observations: {len(records)}")
    print(f"Unique auction IDs: {len(rows)}")
    print(f"Rows with official source links: {sum(bool(r['detail_links'] or r['source_url'] or r['report_url']) for r in rows)}")
    print("Coordinates auto-generated: 0 (intentional; no authoritative spatial evidence was supplied)")
    print(f"Created: {csv_path}")
    print(f"Created: {json_path}")

if __name__ == "__main__":
    main()
