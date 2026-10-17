#!/usr/bin/env python3
"""Stage JDA institutional land-allotment history from its official index."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "authority_staging"
RAW = OUT / "raw_samples"
URL = "https://jdaservice.rajasthan.gov.in/jdaMicroportalProject/landbank/InstitutionalAllotments"
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


def parse_row(values: list[str], checked: datetime) -> dict[str, str]:
    if len(values) != 9 or values[0].casefold() in {"sr no", "sr. no"}:
        raise ValueError(f"Unexpected JDA institutional row: {values!r}")
    serial, zone, institution_type, institution, scheme, area, date, rate, purpose = values
    if not serial.isdigit():
        raise ValueError(f"Invalid JDA institutional serial: {serial!r}")
    return {
        "record_key": f"jda-institutional-allotment:{serial}",
        "authority": "Jaipur Development Authority",
        "source_type": "institutional land allotment",
        "source_url": URL,
        "original_identifier": serial,
        "title": institution,
        "property_or_plot_number": scheme,
        "scheme_or_industrial_area": scheme,
        "land_use_category": purpose,
        "area": area,
        "bid_start": "",
        "reserve_price_or_rate": rate,
        "emd": "",
        "application_start": "",
        "application_end": "",
        "bid_end": date,
        "current_status": "historical",
        "last_checked_at": checked.isoformat(),
        "dedupe_key": f"jda|institutional-allotment|{serial}",
        "review_status": "official_index_record",
        "notes": (
            f"Official JDA institutional allotment row; zone={zone}; "
            f"institution type={institution_type}; allotment date={date}. "
            "This is an allotment to an institution, not land available for purchase."
        ),
    }


def main() -> int:
    checked = datetime.now(IST).replace(microsecond=0)
    response = requests.get(URL, headers={"User-Agent": "JLI JDA allotment collector"}, timeout=60)
    response.raise_for_status()
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "jda_institutional_allotments.html").write_text(response.text, encoding="utf-8")
    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("JDA institutional allotments table not found")
    records = []
    for tr in table.find_all("tr")[1:]:
        values = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        if values:
            records.append(parse_row(values, checked))
    if len({r["original_identifier"] for r in records}) != len(records):
        raise ValueError("Duplicate JDA institutional allotment serials")
    with (OUT / "jda_institutional_allotments.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)
    (OUT / "jda_institutional_allotments.json").write_text(
        json.dumps({"generated_at": checked.isoformat(), "source_url": URL, "records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"JDA institutional allotments: {len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
