#!/usr/bin/env python3
"""Stage Jaipur RHB allotment notices linked from the official draw index."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "authority_staging"
RAW = OUT / "raw_samples"
INDEX_URL = "https://rhb.rajasthan.gov.in/content/raj/rhb/en/draws/latest-draws---2019.html"
BASE_URL = "https://rhb.rajasthan.gov.in"
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


def parse_date(value: str) -> datetime | None:
    for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def parse_row(cells: list[str], href: str, checked: datetime) -> dict[str, str] | None:
    if len(cells) < 3 or not href:
        return None
    title = clean(cells[1])
    if not re.search(r"JAIPUR|MANSAROVAR|JAGATPURA|PRATAP NAGAR", title, re.I):
        return None
    draw_date = clean(cells[2])
    parsed = parse_date(draw_date)
    status = "historical" if parsed is not None and parsed <= checked else (
        "upcoming_notice" if parsed is not None else "uncertain"
    )
    document_url = urljoin(BASE_URL, href)
    identifier = hashlib.sha256(document_url.encode("utf-8")).hexdigest()[:20]
    return {
        "record_key": f"rhb-allotment-draw:{identifier}",
        "authority": "Rajasthan Housing Board",
        "source_type": "scheme allotment notice",
        "source_url": document_url,
        "original_identifier": identifier,
        "title": title,
        "property_or_plot_number": "",
        "scheme_or_industrial_area": title,
        "land_use_category": "Housing/property allotment",
        "area": "",
        "bid_start": "",
        "reserve_price_or_rate": "",
        "emd": "",
        "application_start": "",
        "application_end": "",
        "bid_end": draw_date,
        "current_status": status,
        "last_checked_at": checked.isoformat(),
        "dedupe_key": f"rhb|allotment-draw|{document_url.casefold()}",
        "review_status": "official_index_and_linked_pdf",
        "notes": (
            f"Draw date from the official index: {draw_date}. "
            "This is an allotment/lottery notice, not a currently available "
            "auction listing. Plot number, area, price and application terms "
            "require extraction from the linked PDF and remain unverified. "
            + ("The index date format is invalid and status is uncertain." if parsed is None else "")
        ),
    }


def main() -> int:
    checked = datetime.now(IST).replace(microsecond=0)
    response = requests.get(
        INDEX_URL, headers={"User-Agent": "JLI RHB allotment collector"}, timeout=60
    )
    response.raise_for_status()
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "rhb_allotment_draw_index.html").write_text(response.text, encoding="utf-8")
    soup = BeautifulSoup(response.text, "html.parser")
    records: list[dict[str, str]] = []
    for tr in soup.find("table").find_all("tr"):
        cells = [clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
        anchor = tr.find("a", href=True)
        record = parse_row(cells, anchor["href"] if anchor else "", checked)
        if record:
            records.append(record)
    identifiers = [r["original_identifier"] for r in records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate linked RHB allotment documents found")
    # Preserve a small set of linked official PDFs as evidence without downloading 140 documents.
    for index, record in enumerate(records[:3], start=1):
        pdf = requests.get(record["source_url"], timeout=60)
        pdf.raise_for_status()
        if not pdf.headers.get("content-type", "").lower().startswith("application/pdf"):
            raise ValueError(f"RHB linked document is not a PDF: {record['source_url']}")
        (RAW / f"rhb_allotment_document_{index}.pdf").write_bytes(pdf.content)
    with (OUT / "rhb_allotment_draw_records.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)
    (OUT / "rhb_allotment_draw_records.json").write_text(
        json.dumps(
            {"generated_at": checked.isoformat(), "index_url": INDEX_URL, "records": records},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"RHB Jaipur allotment notices: {len(records)}")
    print(f"Historical: {sum(r['current_status'] == 'historical' for r in records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
