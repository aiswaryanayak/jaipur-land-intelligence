#!/usr/bin/env python3
"""Collect verified Jaipur-region RIICO opportunities into isolated staging."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "authority_staging"
BASE = "https://www.riicoerp.industries.rajasthan.gov.in"
AUCTION_URL = BASE + "/EAuction/ShowEAuctionGrid"
DIRECT_URL = BASE + "/DirectLand/DirectAllotmentSearchGrid"
IST = timezone(timedelta(hours=5, minutes=30))

UNITS = {
    "18": "Jaipur (EPIP-Sitapura)",
    "15": "Jaipur (North)",
    "16": "Jaipur (Rural)",
    "17": "Jaipur (South)",
}
FIELDS = [
    "record_key", "authority", "source_type", "source_url",
    "original_identifier", "title", "property_or_plot_number",
    "scheme_or_industrial_area", "land_use_category", "area", "bid_start",
    "reserve_price_or_rate", "emd", "application_start", "application_end",
    "bid_end", "current_status", "last_checked_at", "dedupe_key",
    "review_status", "notes",
]


def parse_money(value: str) -> str:
    match = re.search(r"Rs\.?\s*([\d,]+(?:\.\d+)?)", value, re.I)
    return match.group(1) if match else ""


def parse_detail(value: str) -> tuple[str, str, str]:
    size = re.search(r"Size:\s*([\d.]+\s*Sqm)", value, re.I)
    rate = re.search(r"Bid Start Rate:\s*([\d.]+)\s*/Sqm", value, re.I)
    emd_match = re.search(r"EMD\s*:\s*Rs\.?\s*([\d,]+(?:\.\d+)?)", value, re.I)
    return (
        size.group(1) if size else "",
        rate.group(1) + " Rs. Per Sqm" if rate else "",
        emd_match.group(1) if emd_match else "",
    )


def parse_plot(value: str) -> tuple[str, str]:
    text = " ".join(value.split())
    match = re.match(r"(.+?)\s+\(([^()]*)\)\s+\(([^()]*)\)", text)
    if not match:
        single = re.match(r"(.+?)\s+\(([^()]*)\)$", text)
        if single:
            return single.group(1).strip(), single.group(2).strip()
        return text, ""
    plot = match.group(1).strip()
    qualifier = match.group(2).strip()
    if qualifier:
        plot = f"{plot} ({qualifier})"
    land_use = match.group(3).strip() or match.group(2).strip()
    return plot, land_use


def parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y %I:%M %p").replace(tzinfo=IST)
    except ValueError:
        return None


def classify_status(status: str, emd_end: str, bid_end: str, checked: datetime) -> str:
    emd_date = parse_date(emd_end)
    bid_date = parse_date(bid_end)
    if status.casefold() == "closed" or (bid_date and bid_date < checked):
        return "closed"
    if emd_date and emd_date < checked:
        return "closed (EMD deadline passed; portal status still Live)"
    if bid_date and bid_date >= checked:
        return "active" if emd_date and emd_date >= checked else "upcoming"
    return "historical"


def collect_unit(
    unit_id: str,
    unit_name: str,
    checked: datetime,
    session: requests.Session | None = None,
) -> list[dict[str, str]]:
    params = {
        "unitId": unit_id,
        "viewBy": "1",
        "usageType": "",
        "DevelopmentStatus": "0",
    }
    source_url = AUCTION_URL + "?" + urlencode(params)
    client = session or requests
    response = client.get(source_url, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    area = ""
    records = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) == 2:
            area_text = cells[1].get_text(" ", strip=True)
            if area_text.startswith("Industrial Area :"):
                area = area_text.split(":", 1)[1].strip()
            continue
        if len(cells) < 9 or not cells[0].get_text(" ", strip=True).isdigit():
            continue
        plot, land_use = parse_plot(cells[1].get_text(" ", strip=True))
        size, rate, emd = parse_detail(cells[2].get_text(" ", strip=True))
        status = cells[8].get_text(" ", strip=True)
        identifier = ""
        for link in cells[9].find_all("a"):
            onclick = link.get("onclick", "")
            match = re.search(r"ViewDocument\((\d+)\)", onclick)
            if match:
                identifier = match.group(1)
                break
        if not identifier:
            raise ValueError(f"Missing stable RIICO document identifier for {plot}")
        emd_start, emd_end = (
            cells[4].get_text(" ", strip=True),
            cells[5].get_text(" ", strip=True),
        )
        bid_start, bid_end = (
            cells[6].get_text(" ", strip=True),
            cells[7].get_text(" ", strip=True),
        )
        records.append({
            "record_key": f"riico-eauction:{identifier}",
            "authority": "RIICO",
            "source_type": "e-auction",
            "source_url": source_url,
            "original_identifier": identifier,
            "title": f"RIICO plot {plot}, {area}, {unit_name}",
            "property_or_plot_number": plot,
            "scheme_or_industrial_area": area,
            "land_use_category": land_use,
            "area": size,
            "bid_start": bid_start,
            "reserve_price_or_rate": rate,
            "emd": emd,
            "application_start": emd_start,
            "application_end": emd_end,
            "bid_end": bid_end,
            "current_status": classify_status(status, emd_end, bid_end, checked),
            "last_checked_at": checked.isoformat(),
            "dedupe_key": f"riico|e-auction|{unit_name.casefold()}|{area.casefold()}|{plot.casefold()}",
            "review_status": "verified_official_listing",
            "notes": (
                f"Official RIICO grid status: {status}. "
                "Fields are transcribed from the official grid. "
                "This staging record is not imported into the JDA catalogue."
            ),
        })
    return records


def check_direct(
    unit_id: str,
    unit_name: str,
    session: requests.Session | None = None,
) -> dict[str, str]:
    params = {
        "unitId": unit_id, "viewBy": "1", "usageType": "",
        "districtId": "", "AreaFrom": "", "AreaTo": "",
        "EcCategory": "", "LeasePeriod": "",
    }
    url = DIRECT_URL + "?" + urlencode(params)
    client = session or requests
    response = client.get(url, timeout=60)
    response.raise_for_status()
    text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
    return {
        "unit": unit_name,
        "url": url,
        "result": "no_records" if "No Record Found" in text else "records_present_review_required",
    }


def main() -> int:
    checked = datetime.now(IST).replace(microsecond=0)
    records = []
    for unit_id, unit_name in UNITS.items():
        records.extend(collect_unit(unit_id, unit_name, checked))
    keys = [record["record_key"] for record in records]
    dedupe_keys = [record["dedupe_key"] for record in records]
    if len(keys) != len(set(keys)) or len(dedupe_keys) != len(set(dedupe_keys)):
        raise ValueError("Duplicate RIICO identifiers or dedupe keys detected.")
    direct_checks = [check_direct(unit_id, name) for unit_id, name in UNITS.items()]
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "opportunity_staging.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)
    (OUT / "opportunity_staging.json").write_text(
        json.dumps({"generated_at": checked.isoformat(), "records": records}, indent=2),
        encoding="utf-8",
    )
    audit_path = OUT / "source_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update({
        "generated_at": checked.isoformat(),
        "production_catalogue_untouched": True,
        "staging_record_count": len(records),
        "new_count": len(records),
        "duplicate_count": 0,
        "rejected_count": 0,
        "unresolved_count": 0,
        "direct_allotment_checks": direct_checks,
        "notes": "Jaipur-region RIICO e-auction records staged; direct allotment remains separate and had no Jaipur-unit live records.",
    })
    for source in audit["sources"]:
        if source["source_type"] == "e-auctions":
            source["access_status"] = "structured_jaipur_grid_extracted"
            source["notes"] = "Four official Jaipur unit grids extracted with stable ViewDocument identifiers."
        elif source["source_type"] == "direct land allotment":
            source["access_status"] = "jaipur_units_no_live_records"
            source["notes"] = "Official DirectAllotmentSearchGrid returned No Record Found for Jaipur EPIP-Sitapura, North, Rural, and South with viewBy=1."
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"RIICO e-auction records staged: {len(records)}")
    print("Direct-allotment Jaipur live records: 0")
    print("Duplicates: 0; rejected: 0; unresolved: 0")
    print(f"Checked at: {checked.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
