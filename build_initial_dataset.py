#!/usr/bin/env python3
"""Assemble the initial read-only opportunity dataset and source coverage."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "authority_staging"
RUN = ROOT / "data" / "refresh_pipeline" / "latest.json"
IST = timezone(timedelta(hours=5, minutes=30))

DATA_FIELDS = [
    "record_key", "authority", "issuing_department", "opportunity_type",
    "status", "official_title", "stable_source_identifier", "scheme",
    "industrial_area", "locality", "district", "village", "plot_property_number",
    "area", "reserve_price_or_rate", "auctioned_rate", "emd",
    "application_start", "application_end", "bid_start", "bid_end",
    "source_publication_date", "source_url", "document_url",
    "last_observed_at", "evidence_status", "location_confidence",
    "missing_field_notes", "raw_evidence_reference",
]


def clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def parse_date(value: str) -> datetime | None:
    for fmt in ("%d-%b-%Y %I:%M %p", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(value.strip(), fmt)
            return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed
        except (ValueError, AttributeError):
            continue
    return None


def availability(emd_end: str, bid_end: str, portal_status: str, now: datetime) -> str:
    bid = parse_date(bid_end)
    emd = parse_date(emd_end)
    if bid and bid < now:
        return "closed"
    if emd and emd < now:
        return "closed (EMD deadline passed; portal status stale)"
    if bid and bid >= now:
        return "active" if emd and emd >= now else "upcoming"
    return "uncertain"


def jda_rows(db_path: Path, now: datetime) -> list[dict[str, Any]]:
    rows = []
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        for row in db.execute(
            """SELECT auction_id,title,scheme_name,property_number,property_area,
            usage_type,assessed_value,bid_start,bid_end,emd_start,emd_end,
            emd_amount,source_authority,source_url,retrieved_at_utc,record_json
            FROM auctions ORDER BY auction_id"""
        ):
            (
                aid, title, scheme, plot, area, usage, assessed, bid_start,
                bid_end, emd_start, emd_end, emd, authority, source_url,
                retrieved, raw_json,
            ) = row
            rows.append({
                "record_key": f"jda-auction:{aid}",
                "authority": authority or "Jaipur Development Authority",
                "issuing_department": "Urban Development and Housing Department, Rajasthan",
                "opportunity_type": "auction",
                "status": availability(emd_end or "", bid_end or "", "", now),
                "official_title": title,
                "stable_source_identifier": aid,
                "scheme": scheme,
                "industrial_area": "",
                "locality": "",
                "district": "Jaipur",
                "village": "",
                "plot_property_number": plot,
                "area": area,
                "reserve_price_or_rate": assessed,
                "auctioned_rate": "",
                "emd": emd,
                "application_start": emd_start,
                "application_end": emd_end,
                "bid_start": bid_start,
                "bid_end": bid_end,
                "source_publication_date": "",
                "source_url": source_url,
                "document_url": "",
                "last_observed_at": retrieved,
                "evidence_status": "canonical catalogue record",
                "location_confidence": "unknown",
                "missing_field_notes": "Publication date, document URL, and explicit locality not present in canonical row.",
                "raw_evidence_reference": raw_json,
            })
    return rows


def riico_rows(run_manifest: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    path = ROOT / run_manifest["staged_files"]["riico_eauction"]
    records = json.loads(path.read_text(encoding="utf-8"))["records"]
    rows = []
    for record in records:
        status = availability(
            record.get("application_end", ""),
            record.get("bid_end", ""),
            record.get("current_status", ""),
            now,
        )
        rows.append({
            "record_key": record["record_key"],
            "authority": "RIICO",
            "issuing_department": "Rajasthan State Industrial Development and Investment Corporation",
            "opportunity_type": "e-auction",
            "status": status,
            "official_title": record["title"],
            "stable_source_identifier": record["original_identifier"],
            "scheme": "",
            "industrial_area": record["scheme_or_industrial_area"],
            "locality": "",
            "district": "Jaipur / Dausa region",
            "village": "",
            "plot_property_number": record["property_or_plot_number"],
            "area": record["area"],
            "reserve_price_or_rate": record["reserve_price_or_rate"],
            "auctioned_rate": "",
            "emd": record["emd"],
            "application_start": record["application_start"],
            "application_end": record["application_end"],
            "bid_start": record["bid_start"],
            "bid_end": record["bid_end"],
            "source_publication_date": "",
            "source_url": record["source_url"],
            "document_url": f"{record['source_url'].split('/EAuction/')[0]}/EAuction/ViewDocument?propertyId={record['original_identifier']}",
            "last_observed_at": record["last_checked_at"],
            "evidence_status": "official grid transcription; document index requires review",
            "location_confidence": "unknown",
            "missing_field_notes": "Plot geometry, locality/village and source publication date not captured from grid.",
            "raw_evidence_reference": json.dumps(record, ensure_ascii=False, sort_keys=True),
        })
    return rows


def post_auction_rows(run_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    path = ROOT / run_manifest["staged_files"]["jda_post_auction"]
    records = json.loads(path.read_text(encoding="utf-8"))["records"]
    rows = []
    for record in records:
        rows.append({
            "record_key": f"jda-post-auction:{record['record_fingerprint']}",
            "authority": "Jaipur Development Authority",
            "issuing_department": "Jaipur Development Authority",
            "opportunity_type": "completed auction result",
            "status": "historical",
            "official_title": f"{record.get('scheme_name') or 'JDA'} plot {record.get('plot_number') or ''}".strip(),
            "stable_source_identifier": record["record_fingerprint"],
            "scheme": record.get("scheme_name"),
            "industrial_area": "",
            "locality": record.get("zone_name"),
            "district": "Jaipur",
            "village": "",
            "plot_property_number": record.get("plot_number"),
            "area": f"{record.get('area_sqm')} Sqm" if record.get("area_sqm") is not None else "",
            "reserve_price_or_rate": "",
            "auctioned_rate": record.get("auctioned_rate_inr_per_sqm"),
            "emd": "",
            "application_start": "",
            "application_end": "",
            "bid_start": record.get("auction_date"),
            "bid_end": record.get("auction_date"),
            "source_publication_date": record.get("auction_date"),
            "source_url": record.get("source_url"),
            "document_url": "",
            "last_observed_at": record.get("observed_at_utc") or run_manifest["checked_at"],
            "evidence_status": "official post-auction table; completed outcome",
            "location_confidence": "unknown",
            "missing_field_notes": "No application/EMD deadlines or document URL in post-auction table.",
            "raw_evidence_reference": json.dumps(record, ensure_ascii=False, sort_keys=True),
        })
    return rows


def source_matrix(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [
        ("Jaipur Development Authority", "auction", "https://udhonline.rajasthan.gov.in/Portal/AuctionListNew", "canonical JDA catalogue", "238", "success", "Existing 238 canonical records; UDH detail crawl remains rate-limited."),
        ("Jaipur Development Authority", "completed auction result", "https://jdaservice.rajasthan.gov.in/jdamicroportalproject/NoticeBoard/PostAuction", "JDA post-auction parser", "8439", "success", "Historical outcomes; not current offers."),
        ("RIICO", "e-auction", "https://www.riicoerp.industries.rajasthan.gov.in/eauction", "RIICO refresh collector", "45", "success", "Five units checked; all currently closed by EMD deadline."),
        ("RIICO", "direct allotment", "https://riico.rajasthan.gov.in/DLA.aspx", "RIICO direct-allotment checks", "0", "success", "Jaipur and Dausa live/upcoming views returned no records."),
        ("JDA / UDH", "scheme and GIS references", "https://jdafa.rajasthan.gov.in/scheme", "research files and location database", "5", "partial", "Scheme/location evidence exists; unresolved labels remain."),
        ("Rajasthan Housing Board", "auction/allotment/disposal", "https://rhb.rajasthan.gov.in/content/raj/rhb/en/home.html", "not implemented", "0", "audited", "Official site reachable; record structure and land-specific endpoint not yet verified."),
        ("Urban GIS / UDH", "GIS and location reference", "https://rajdharaa.rajasthan.gov.in/citizenudh/index", "not implemented", "0", "audited", "Official GIS portal reachable; no opportunity records collected."),
        ("Local Self Government Rajasthan", "municipal disposal/notices", "https://lsg.rajasthan.gov.in/lsg/", "not implemented", "0", "audited", "Portal reachable, but no land-specific Jaipur record feed verified."),
        ("Rajasthan e-Procurement", "tender", "https://eproc.rajasthan.gov.in/nicgep/app", "not implemented", "0", "audited", "Generic tender portal; land-relevant search and pagination not verified."),
        ("Rajasthan acquisition/public notices", "acquisition/notification", "https://jda.rajasthan.gov.in/", "not implemented", "0", "not examined", "No acquisition dataset added; notices must remain separate from opportunities."),
    ]
    counts = {}
    for row in rows:
        key = (row["authority"], row["opportunity_type"])
        counts[key] = counts.get(key, 0) + 1
    output = []
    for authority, typ, url, collector, stated, status, notes in sources:
        count = counts.get((authority, typ), 0)
        actual = str(count) if count else stated
        relevant = [r for r in rows if r["authority"] == authority and r["opportunity_type"] == typ]
        output.append({
            "authority": authority, "opportunity_type": typ, "official_url": url,
            "collector_or_input": collector, "records_collected": actual,
            "active_or_upcoming": sum(r["status"] in {"active", "upcoming"} for r in relevant),
            "closed_or_historical": sum(r["status"].startswith("closed") or r["status"] == "historical" for r in relevant),
            "uncertain": sum(r["status"] == "uncertain" for r in relevant),
            "verification": status, "last_successful_fetch": manifest.get("checked_at", ""),
            "limitations_next_action": notes,
        })
    return output


def main() -> int:
    manifest = json.loads(RUN.read_text(encoding="utf-8"))
    now = datetime.now(IST)
    rows = jda_rows(ROOT / "data" / "jaipur_land_intelligence.sqlite3", now)
    rows.extend(riico_rows(manifest, now))
    rows.extend(post_auction_rows(manifest))
    keys = [row["record_key"] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate unified record keys detected")
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "initial_dataset.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DATA_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (OUT / "initial_dataset.json").write_text(
        json.dumps({"generated_at": now.isoformat(), "records": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    matrix = source_matrix(rows, manifest)
    with (OUT / "source_coverage_matrix.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(matrix[0]))
        writer.writeheader()
        writer.writerows(matrix)
    status_counts = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    report = [
        "# Initial opportunity dataset report",
        "",
        f"Generated: `{now.isoformat()}`",
        "",
        "## Dataset totals",
        "",
        f"- Unified records: **{len(rows)}**",
        f"- Canonical JDA auction records: **{len([r for r in rows if r['opportunity_type'] == 'auction'])}**",
        f"- RIICO e-auctions: **{len([r for r in rows if r['opportunity_type'] == 'e-auction'])}**",
        f"- JDA completed auction results: **{len([r for r in rows if r['opportunity_type'] == 'completed auction result'])}**",
        f"- Unique unified record keys: **{len(set(keys))}**",
        f"- Status counts: `{status_counts}`",
        "",
        "The 8,439 post-auction rows are historical outcomes and are not presented as current opportunities. All 45 RIICO rows were recalculated from application/EMD and bid deadlines; at collection time they were closed because the EMD deadline had passed despite the portal showing `Live`.",
        "",
        "## Deduplication",
        "",
        "JDA catalogue IDs, RIICO source identifiers, and JDA post-auction fingerprints are kept in authority/type-specific record keys. The UDH live index was not appended as a second copy because it is a partial identifier-only discovery and overlaps the existing catalogue scope.",
        "",
        "## Field completeness and evidence",
        "",
        "Every unified row has an authority, opportunity type, stable source key, title, status, source URL, observation timestamp or run timestamp, and raw evidence reference. Missing deadlines, publication dates, document URLs, locality, village, and exact location data remain explicitly noted rather than inferred.",
        "",
        "## Coverage gaps",
        "",
        "- Rajasthan Housing Board was reachable but no land-specific record structure was verified.",
        "- Rajdharaa GIS was reachable but supplies reference GIS rather than an opportunity feed; no coordinates were invented.",
        "- LSG Rajasthan was reachable but no Jaipur land-disposal record feed was verified.",
        "- Rajasthan e-Procurement was reachable but generic tender search/pagination and land relevance were not verified.",
        "- Acquisition and public-notification records were not collected and remain separate from opportunities.",
        "- UDH/JDA record-level detail crawling encountered HTTP 429; this is reported as a partial source result, not cancellation.",
        "",
        "## Preservation and validation",
        "",
        "- Existing SQLite catalogue and location rows were read-only.",
        "- Existing RIICO staging files and prior audit reports were not overwritten.",
        "- No records were promoted into the main catalogue.",
    ]
    (OUT / "initial_dataset_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"unified_records={len(rows)} status_counts={status_counts} sources={len(matrix)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
