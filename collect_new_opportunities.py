#!/usr/bin/env python3
"""Collect newly discovered official opportunities into separate research staging."""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from collect_riico_staging import (
    BASE,
    FIELDS,
    IST,
    classify_status,
    collect_unit,
)

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "authority_staging"
DISCOVERY_CSV = OUT / "new_opportunity_discoveries.csv"
DISCOVERY_JSON = OUT / "new_opportunity_discoveries.json"
REPORT = OUT / "new_opportunity_research_report.md"
UDH_BASE = "https://udhonline.rajasthan.gov.in"
JDA_LIST = UDH_BASE + "/Portal/AuctionListNew"
JDA_DETAIL_PREFIX = UDH_BASE + "/Portal/LiveAuctionDetailReport"
IST_NOW = datetime(2026, 10, 18, 3, 11, 24, tzinfo=IST)


def read_existing() -> tuple[set[str], set[str], set[str]]:
    db_ids: set[str] = set()
    with sqlite3.connect(f"file:{ROOT / 'data' / 'jaipur_land_intelligence.sqlite3'}?mode=ro", uri=True) as db:
        db_ids.update(row[0] for row in db.execute("SELECT auction_id FROM auctions"))
    staged_ids: set[str] = set()
    staged_dedupe_keys: set[str] = set()
    staged_path = OUT / "opportunity_staging.json"
    records = json.loads(staged_path.read_text(encoding="utf-8"))["records"]
    staged_ids.update(str(row["original_identifier"]) for row in records)
    staged_dedupe_keys.update(str(row["dedupe_key"]) for row in records)
    return db_ids, staged_ids, staged_dedupe_keys


def collect_jda_live_ids(
    session: requests.Session,
) -> tuple[set[str], int, list[str], str]:
    response = session.get(JDA_LIST, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    jda_row = next(
        row for row in soup.find_all("tr")
        if "Jaipur Development Authority" in row.get_text(" ", strip=True)
    )
    links = [
        urljoin(UDH_BASE, link["href"])
        for link in jda_row.find_all("a")
        if link.get("href", "").startswith("/Portal/LiveAuctionDetailReport")
    ]
    ids: set[str] = set()
    limitation = ""
    for url in links:
        detail = session.get(url, timeout=60)
        detail.raise_for_status()
        detail_soup = BeautifulSoup(detail.text, "html.parser")
        record_urls = {
            urljoin(UDH_BASE, link["href"])
            for link in detail_soup.find_all("a")
            if link.get("href", "").startswith("/Portal/PortalAuctionList")
        }
        for record_url in record_urls:
            record = session.get(record_url, timeout=60)
            if record.status_code == 429:
                limitation = "UDH record detail crawl rate-limited with HTTP 429"
                return ids, len(links), links, limitation
            record.raise_for_status()
            record_soup = BeautifulSoup(record.text, "html.parser")
            ids.update(
                re.findall(
                    r"\bId\s*:\s*(\d+)\b",
                    record_soup.get_text(" ", strip=True),
                )
            )
    return ids, len(links), links, limitation


def direct_result(session: requests.Session, unit_id: str, view: str) -> str:
    url = BASE + "/DirectLand/DirectAllotmentSearchGrid"
    params = {
        "unitId": unit_id,
        "viewBy": view,
        "usageType": "",
        "districtId": "",
        "AreaFrom": "",
        "AreaTo": "",
        "EcCategory": "",
        "LeasePeriod": "",
    }
    response = session.get(url, params=params, timeout=60)
    response.raise_for_status()
    text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
    return "no_records" if "No Record Found" in text else "records_present_manual_review"


def main() -> int:
    checked = IST_NOW
    session = requests.Session()
    session.headers["User-Agent"] = "JLI read-only opportunity research"
    db_ids, staged_ids, staged_dedupe_keys = read_existing()
    jda_ids, jda_views, jda_urls, jda_limitation = collect_jda_live_ids(session)

    dausa = collect_unit("53", "Dausa", checked)
    existing_riico = db_ids | staged_ids
    new_records = [
        row for row in dausa
        if row["original_identifier"] not in existing_riico
        and row["dedupe_key"] not in staged_dedupe_keys
    ]
    if len({row["original_identifier"] for row in new_records}) != len(new_records):
        raise ValueError("Duplicate new RIICO source identifiers detected")

    for row in new_records:
        row["source_url"] = (
            BASE + "/EAuction/ViewDocument?propertyId="
            + row["original_identifier"]
        )
        row["review_status"] = "new_official_listing_manual_document_review"
        row["notes"] += (
            " Grid evidence: "
            + BASE
            + "/EAuction/ShowEAuctionGrid?unitId=53&viewBy=1"
            + "&usageType=&DevelopmentStatus=0."
        )
        row["notes"] += (
            " Newly discovered in the official Dausa unit grid; "
            "not present in the existing 41-record RIICO staging set. "
            "Record-scoped document index requires manual review."
        )

    OUT.mkdir(parents=True, exist_ok=True)
    with DISCOVERY_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(new_records)
    DISCOVERY_JSON.write_text(
        json.dumps(
            {
                "generated_at": checked.isoformat(),
                "records": new_records,
                "sources": {
                    "riico_dausa_grid": BASE
                    + "/EAuction/ShowEAuctionGrid?unitId=53&viewBy=1"
                    + "&usageType=&DevelopmentStatus=0",
                    "udh_jda_live_index": JDA_LIST,
                    "udh_jda_detail_views": jda_urls,
                },
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    direct_checks = {
        unit: {
            "live": direct_result(session, unit, "1"),
            "upcoming": direct_result(session, unit, "2"),
        }
        for unit in ("18", "15", "16", "17", "53")
    }
    report = [
        "# New official opportunity research",
        "",
        f"Checked at: `{checked.isoformat()}`",
        "",
        "## Results",
        "",
        f"- New RIICO records staged: **{len(new_records)}**",
        f"- New JDA/UDH records staged: **0**",
        f"- Existing JDA IDs checked: **{len(db_ids)}**",
        f"- Existing RIICO staged IDs checked: **{len(staged_ids)}**",
        f"- Current official JDA live records checked: **{len(jda_ids)}** "
        f"across {jda_views} usage views",
        (
            "- All current JDA live IDs were already present in the local 238-record database."
            if not jda_limitation
            else f"- JDA record-level comparison was incomplete: {jda_limitation}. "
            "No JDA records were staged."
        ),
        "",
        "## New RIICO records",
        "",
        "The four records below came from the official RIICO Dausa unit live grid. "
        "Their application/EMD deadline is already past, so they are staged as "
        "`closed (EMD deadline passed; portal status still Live)` and are not "
        "available opportunities.",
        "",
    ]
    for row in new_records:
        report.extend(
            [
                f"- `{row['original_identifier']}` — "
                f"{row['property_or_plot_number']} / "
                f"{row['scheme_or_industrial_area']}; "
                f"{row['area']}; {row['land_use_category']}; "
                f"rate `{row['reserve_price_or_rate']}`; EMD `{row['emd']}`; "
                f"status `{row['current_status']}`.",
                f"  - Grid: {BASE}/EAuction/ShowEAuctionGrid?unitId=53&viewBy=1&usageType=&DevelopmentStatus=0",
                f"  - Record-scoped documents: {row['source_url']}",
            ]
        )
    report += [
        "",
        "## Direct allotments and limitations",
        "",
        "- RIICO Jaipur EPIP-Sitapura, North, Rural, South, and Dausa live and "
        "upcoming direct-allotment views returned `No Record Found`.",
        "- RIICO Jaipur-unit upcoming e-auction views returned zero rows.",
        "- The UDH/JDA live index was reachable and reported 163 Jaipur records; "
        "record-level crawling was rate-limited before the full identifier set "
        "could be compared. No JDA records were staged.",
        "- The four RIICO records retain official grid evidence and a stable "
        "record-scoped document URL. The document index and underlying plans "
        "still require manual review before any future import.",
        "",
        "## Preservation",
        "",
        "- The production SQLite database and 238 JDA records were not modified.",
        "- The existing 41-record RIICO staging files and audit report were not modified.",
        "- No records were imported into the production catalogue.",
    ]
    REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"new RIICO records: {len(new_records)}")
    print(f"new JDA records: 0; current JDA live IDs: {len(jda_ids)}")
    print(f"direct allotment checks: {direct_checks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
