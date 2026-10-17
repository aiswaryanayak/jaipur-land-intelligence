#!/usr/bin/env python3
"""Collect explicitly land-related RHB tender/e-auction notices into staging."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "authority_staging"
RAW = OUT / "raw_samples"
URL = "https://rhb.rajasthan.gov.in/content/raj/rhb/en/core-business/Tenders.html"
IST = timezone(timedelta(hours=5, minutes=30))
FIELDS = [
    "record_key", "authority", "source_type", "source_url",
    "original_identifier", "title", "property_or_plot_number",
    "scheme_or_industrial_area", "land_use_category", "area",
    "bid_start", "reserve_price_or_rate", "emd", "application_start",
    "application_end", "bid_end", "current_status", "last_checked_at",
    "dedupe_key", "review_status", "notes",
]


def clean(value: str) -> str:
    return " ".join(value.split())


def relevant(description: str) -> bool:
    return bool(re.search(r"\be[- ]auction\b|\bauction\b", description, re.I))


def parse_row(cells: list[str], checked: datetime) -> dict[str, str] | None:
    if len(cells) < 7 or cells[0].lower() in {"sr no", "sr. no."}:
        return None
    description = clean(cells[1])
    if not relevant(description):
        return None
    identifier = clean(cells[2])
    if not identifier:
        return None
    return {
        "record_key": f"rhb-tender:{identifier}",
        "authority": "Rajasthan Housing Board",
        "source_type": "historical e-auction notice",
        "source_url": URL,
        "original_identifier": identifier,
        "title": description,
        "property_or_plot_number": "",
        "scheme_or_industrial_area": "",
        "land_use_category": "Land/property disposal notice",
        "area": "",
        "bid_start": "",
        "reserve_price_or_rate": "",
        "emd": "",
        "application_start": clean(cells[4]),
        "application_end": clean(cells[5]),
        "bid_end": clean(cells[6]),
        "current_status": "historical",
        "last_checked_at": checked.isoformat(),
        "dedupe_key": f"rhb|tender|{identifier.casefold()}",
        "review_status": "official_notice_manual_record_review",
        "notes": (
            "Explicit RHB e-auction notice found in official tender index. "
            "The index does not publish plot identifiers, area, reserve price, "
            "EMD, or a record-specific document link for this row."
        ),
    }


def main() -> int:
    checked = datetime.now(IST).replace(microsecond=0)
    response = requests.get(
        URL, headers={"User-Agent": "JLI RHB land-record collector"}, timeout=60
    )
    response.raise_for_status()
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "rhb_tenders_sample.html").write_text(response.text, encoding="utf-8")
    soup = BeautifulSoup(response.text, "html.parser")
    records = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
            record = parse_row(cells, checked)
            if record:
                records.append(record)
    identifiers = [row["original_identifier"] for row in records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate RHB tender identifiers found")
    with (OUT / "rhb_land_records.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)
    (OUT / "rhb_land_records.json").write_text(
        json.dumps(
            {
                "generated_at": checked.isoformat(),
                "source_url": URL,
                "records": records,
                "excluded_scope": "Construction, maintenance, lighting, GIS, and unrelated service tenders.",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"RHB explicit land/e-auction records: {len(records)}")
    print(f"HTTP status: {response.status_code}; raw sample: {RAW / 'rhb_tenders_sample.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
