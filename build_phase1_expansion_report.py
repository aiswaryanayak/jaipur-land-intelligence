#!/usr/bin/env python3
"""Create preserved, additive Phase 1 coverage artifacts."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "authority_staging"
IST = timezone(timedelta(hours=5, minutes=30))


def main() -> int:
    initial = list(csv.DictReader((OUT / "initial_dataset.csv").open(encoding="utf-8-sig", newline="")))
    rhb = list(csv.DictReader((OUT / "rhb_land_records.csv").open(encoding="utf-8-sig", newline="")))
    rhb_draws = list(csv.DictReader((OUT / "rhb_allotment_draw_records.csv").open(encoding="utf-8-sig", newline="")))
    jda_allotments = list(csv.DictReader((OUT / "jda_institutional_allotments.csv").open(encoding="utf-8-sig", newline="")))
    all_new = rhb + rhb_draws + jda_allotments
    initial_keys = {row["record_key"] for row in initial}
    initial_ids = {row["stable_source_identifier"] for row in initial}
    overlap_keys = [row["record_key"] for row in all_new if row["record_key"] in initial_keys]
    overlap_ids = [row["original_identifier"] for row in all_new if row["original_identifier"] in initial_ids]
    matrix = list(csv.DictReader((OUT / "source_coverage_matrix.csv").open(encoding="utf-8-sig", newline="")))
    now = datetime.now(IST).replace(microsecond=0).isoformat()
    matrix.append({
        "authority": "Rajasthan Housing Board",
        "opportunity_type": "historical e-auction notice",
        "official_url": "https://rhb.rajasthan.gov.in/content/raj/rhb/en/core-business/Tenders.html",
        "collector_or_input": "collect_rhb_land_records.py",
        "records_collected": str(len(rhb)),
        "active_or_upcoming": "0",
        "closed_or_historical": str(sum(row["current_status"] == "historical" for row in rhb)),
        "uncertain": str(sum(row["current_status"] == "uncertain" for row in rhb)),
        "verification": "official index row; manual document review needed",
        "last_successful_fetch": now,
        "limitations_next_action": "The official index exposes no plot, area, rate, EMD, or record-specific document link; locate the underlying 2015 e-auction notice before using it for pricing or availability.",
    })
    matrix.append({
        "authority": "Jaipur Development Authority",
        "opportunity_type": "institutional land allotment",
        "official_url": "https://jdaservice.rajasthan.gov.in/jdaMicroportalProject/landbank/InstitutionalAllotments",
        "collector_or_input": "collect_jda_institutional_allotments.py",
        "records_collected": str(len(jda_allotments)),
        "active_or_upcoming": "0",
        "closed_or_historical": str(len(jda_allotments)),
        "uncertain": "0",
        "verification": "official server-rendered allotment index",
        "last_successful_fetch": now,
        "limitations_next_action": "Historical institutional allocations, not property available for purchase; retain as a separate land-allotment category.",
    })
    matrix.append({
        "authority": "Rajasthan Housing Board",
        "opportunity_type": "scheme allotment notice",
        "official_url": "https://rhb.rajasthan.gov.in/content/raj/rhb/en/draws/latest-draws---2019.html",
        "collector_or_input": "collect_rhb_allotment_draws.py",
        "records_collected": str(len(rhb_draws)),
        "active_or_upcoming": str(sum(row["current_status"] == "upcoming_notice" for row in rhb_draws)),
        "closed_or_historical": str(sum(row["current_status"] == "historical" for row in rhb_draws)),
        "uncertain": str(sum(row["current_status"] == "uncertain" for row in rhb_draws)),
        "verification": "official index and linked PDFs; fields require document-level review",
        "last_successful_fetch": now,
        "limitations_next_action": "These are allotment/lottery notices, not current auctions; extract plot, area, price and eligibility from PDFs before analytical use.",
    })
    with (OUT / "source_coverage_matrix_phase1_expanded.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(matrix[0]))
        writer.writeheader()
        writer.writerows(matrix)
    report = [
        "# Phase 1 authority coverage expansion",
        "",
        f"Generated: `{now}`",
        "",
        "This is an additive report. The original initial dataset, coverage matrix, "
        "RIICO staging files, audit reports, refresh manifests, and SQLite database "
        "were preserved unchanged.",
        "",
        "## Newly collected records",
        "",
        f"- Rajasthan Housing Board explicit historical e-auction notices: **{len(rhb)}**",
        f"- Jaipur RHB scheme-allotment notices linked to official PDFs: **{len(rhb_draws)}**",
        f"- JDA institutional land allotments: **{len(jda_allotments)}**",
        f"- New records versus the 8,722-record initial dataset: **{len(all_new) - len(overlap_keys)}**",
        f"- Stable-key overlaps: **{len(overlap_keys)}**",
        f"- Stable-identifier overlaps: **{len(overlap_ids)}**",
        "- Current/upcoming RHB land opportunities: **0**",
        "- Historical RHB records: **1**",
        f"- Historical RHB allotment notices: **{sum(row['current_status'] == 'historical' for row in rhb_draws)}**",
        f"- RHB notices treated as currently available: **{sum(row['current_status'] == 'upcoming_notice' for row in rhb_draws)}**",
        "- JDA institutional allotments treated as currently available: **0**",
        "",
        "The collected RHB e-auction row is NIT/identifier `1184`, an explicit e-auction "
        "notice dated 2015. It is historical only. The RHB tender index does not "
        "publish a plot number, area, reserve price, EMD, or record-specific "
        "document link for this row, so those fields remain blank.",
        "",
        "The separate allotment file contains 140 Jaipur-related rows from the official "
        "Allotment Draw index. Each has a stable hash of its official PDF URL. They are "
        "historical draw/allotment notices, not active land opportunities. Only three "
        "linked PDFs were downloaded as raw samples; plot, area, price and eligibility "
        "remain unverified pending document extraction.",
        "",
        f"The JDA institutional allotment index contributed {len(jda_allotments)} historical "
        "rows with serial number, institution, scheme/plot description, area, allotment "
        "date, rate, and purpose. These are institutional allocations, not sale listings.",
        "",
        "## Official source audit",
        "",
        "| Source | Result | Next action |",
        "|---|---|---|",
        "| RHB Tender index | HTTP 200; parseable table; 1 explicit e-auction row | Locate underlying notice/document before using details |",
        "| RHB Allotment Draw index | HTTP 200; 140 Jaipur-linked rows and 140 official PDF links; 139 historical and 1 uncertain | Extract PDF fields before using plot/price/eligibility data |",
        "| JDA Auction Programme | HTTP 200; instructions and official UDH link, no record table | Use the existing UDH dataset/cache; do not duplicate the index |",
        "| JDA Institutional Allotments | HTTP 200; 669 structured rows | Keep as historical institutional allocation, not sale availability |",
        "| JDA/UDH live index | Existing summary scope; detail crawl rate-limited with HTTP 429 | Reuse cached pages and wait for a lower-frequency detail pass |",
        "| JDA Public Notices / acquisition | HTTP 200; 7,731 notices, no conservative acquisition-keyword matches | Identify a department-specific acquisition index or document taxonomy |",
        "| Jaipur municipal sources | Jaipur municipal site connection failed during this check; no record staged | Retry through an official LSG/municipal notice index without repeated requests |",
        "| LSG Rajasthan | Portal reachable; no verified Jaipur land-disposal feed | Identify municipal disposal index or notice archive |",
        "| Rajasthan e-Procurement | HTTP 200; advanced search, active, archive, location and classification pages expose stateful POST forms and CAPTCHA fields; no search submitted | Establish a bounded Jaipur land query and pagination contract before collecting |",
        "| Rajdharaa UDH GIS | Portal reachable; reference GIS, not opportunity feed | Use only for location evidence, never as availability source |",
        "",
        "## Evidence files",
        "",
        "- `rhb_land_records.csv` and `rhb_land_records.json` contain the new staged row.",
        "- `rhb_allotment_draw_records.csv` and `.json` contain the separate allotment notices.",
        "- `jda_institutional_allotments.csv` and `.json` contain the separate JDA allotments.",
        "- `raw_samples/rhb_allotment_draw_index.html` and three linked PDFs preserve evidence samples.",
        "- `raw_samples/rhb_tenders_sample.html` preserves the fetched official HTML.",
        "- `source_coverage_matrix_phase1_expanded.csv` is the additive matrix.",
        "",
        "No RHB row was added to the production catalogue or the 8,722-record "
        "initial dataset.",
    ]
    (OUT / "initial_dataset_phase1_expansion_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"rhb_records={len(rhb)} overlap_keys={len(overlap_keys)} overlap_ids={len(overlap_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
