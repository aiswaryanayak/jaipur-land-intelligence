import json
import re
import time
import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://udhonline.rajasthan.gov.in"
CATALOG_PATH = Path("data/udh_test/jda_scheme_discovery.json")
OUT_DIR = Path("data/udh_test")
OUT_PATH = OUT_DIR / "jda_full_catalog_observations.json"
ERROR_PATH = OUT_DIR / "jda_full_catalog_errors.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JaipurLandIntelligence/0.1)"}
DELAY_SECONDS = 0.5
TIMEOUT_SECONDS = 30


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    return " ".join(value.split()).strip()


def valid_listing_url(url):
    p = urlparse(url)
    return (
        p.scheme == "https"
        and p.hostname == "udhonline.rajasthan.gov.in"
        and p.path == "/Portal/PortalAuctionList"
    )


def valid_detail_url(url):
    p = urlparse(url)
    return (
        p.scheme == "https"
        and p.hostname == "udhonline.rajasthan.gov.in"
        and p.path == "/Portal/ViewAuction"
        and bool(p.query)
    )


def row_cells(row):
    return [
        clean(cell.get_text(" ", strip=True))
        for cell in row.find_all(["th", "td"], recursive=False)
    ]


def field_value(text, label):
    match = re.search(
        rf"{re.escape(label)}\s*:\s*(.*?)(?=\s+[A-Za-z][A-Za-z ()/.-]*\s*:|$)",
        text,
        flags=re.IGNORECASE,
    )
    return clean(match.group(1)) if match else None


def parse_listing(html, listing_url):
    soup = BeautifulSoup(html, "html.parser")
    observations = []
    parse_warnings = []

    tables = soup.find_all("table")
    if len(tables) != 1:
        parse_warnings.append(f"Expected 1 table; found {len(tables)}")

    for table_no, table in enumerate(tables, start=1):
        rows = table.find_all("tr")
        for row_index, row in enumerate(rows):
            cells = row_cells(row)
            row_text = clean(row.get_text(" ", strip=True))

            # Auction rows contain an explicit ID label.
            if not __import__("re").search(r"\bId\s*:", row_text, re.IGNORECASE):
                continue

            id_match = re.search(r"\bId\s*:\s*(\d+)", row_text, re.IGNORECASE)
            if not id_match:
                parse_warnings.append(
                    f"Table {table_no}, row {row_index}: ID label found but no numeric ID"
                )
                continue

            detail_links = []
            for link in row.find_all("a", href=True):
                href = urljoin(BASE, link["href"])
                if "/Portal/ViewAuction" in urlparse(href).path:
                    detail_links.append({
                        "label": clean(link.get_text(" ", strip=True)),
                        "url": href,
                        "url_valid": valid_detail_url(href),
                    })

            # Assessed value is usually in the next table row.
            # Only associate it when the following row explicitly says "Assessed Property Value".
            assessed_text = None
            assessed_row_index = None
            if row_index + 1 < len(rows):
                next_row = rows[row_index + 1]
                next_text = clean(next_row.get_text(" ", strip=True))
                if "Assessed Property Value" in next_text:
                    assessed_text = next_text
                    assessed_row_index = row_index + 1

            observations.append({
                "auction_id": id_match.group(1),
                "listing_url": listing_url,
                "table_number": table_no,
                "auction_row_index": row_index,
                "auction_row_text": row_text,
                "auction_row_cells": cells,
                "detail_links": detail_links,
                "assessed_value_row_index": assessed_row_index,
                "assessed_value_row_text": assessed_text,
                "assessed_value_raw": (
                    assessed_text.split(":", 1)[1].split("(INR)", 1)[0].strip()
                    if assessed_text and ":" in assessed_text
                    else None
                ),
                "retrieved_at_utc": now_utc(),
            })

    return observations, parse_warnings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--delay", type=float, default=DELAY_SECONDS)
    parser.add_argument("--progress", type=Path, default=OUT_DIR / "jda_full_catalog_progress.json")
    args = parser.parse_args()

    import refresh_pipeline
    discovery_session = requests.Session()
    discovery_session.headers.update(HEADERS)
    entries = refresh_pipeline.discover_udh_entries(
        discovery_session,
        retries=0,
        interval=args.delay,
        cache_ttl=0,
        use_cache=False,
        persist=True,
    )
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8")) if CATALOG_PATH.exists() else {}
    if args.max_pages is not None:
        entries = entries[:max(0, args.max_pages)]
    if not entries:
        raise SystemExit("No scheme entries found in discovery catalog.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)

    all_observations = []
    errors = []
    scheme_results = []
    progress = {"entries": {}, "updated_at_utc": None}
    if args.progress.exists():
        try:
            loaded = json.loads(args.progress.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                progress.update(loaded)
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    for index, entry in enumerate(entries, start=1):
        listing_url = entry.get("official_listing_url", "")
        scheme_name = entry.get("scheme_name")
        reported_count = entry.get("reported_auction_count")

        result = {
            "catalog_entry_number": index,
            "scheme_name": scheme_name,
            "reported_auction_count": reported_count,
            "listing_url": listing_url,
            "retrieved_at_utc": now_utc(),
            "http_status": None,
            "parsed_row_count": 0,
            "unique_auction_ids_in_page": 0,
            "counts_match": None,
            "parse_warnings": [],
            "error": None,
        }

        print(f"[{index}/{len(entries)}] {scheme_name} | reported={reported_count}")

        if not valid_listing_url(listing_url):
            result["error"] = "Rejected unexpected listing URL"
            errors.append(result.copy())
            scheme_results.append(result)
            continue

        try:
            saved = progress.get("entries", {}).get(listing_url)
            if isinstance(saved, dict) and saved.get("status") == "success":
                observations = saved.get("observations", [])
                for observation in observations:
                    observation["source_authority"] = catalog.get(
                        "source_authority", "Jaipur Development Authority"
                    )
                    observation["catalog_entry_number"] = index
                    observation["catalog_scheme_name"] = scheme_name
                    observation["catalog_reported_auction_count"] = reported_count
                    all_observations.append(observation)
                result["http_status"] = saved.get("http_status", 200)
                result["parsed_row_count"] = len(observations)
                result["unique_auction_ids_in_page"] = len({
                    o["auction_id"] for o in observations
                })
                result["counts_match"] = saved.get("counts_match")
                scheme_results.append(result)
                continue
            response = session.get(listing_url, timeout=TIMEOUT_SECONDS)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "")
                result["error"] = "HTTP 429 rate limited"
                errors.append(result.copy())
                progress.setdefault("entries", {})[listing_url] = {
                    "status": "rate_limited",
                    "retry_after": retry_after,
                }
                args.progress.parent.mkdir(parents=True, exist_ok=True)
                args.progress.write_text(json.dumps(progress, indent=2), encoding="utf-8")
                break
            result["http_status"] = response.status_code
            response.raise_for_status()

            observations, warnings = parse_listing(response.text, listing_url)
            result["parsed_row_count"] = len(observations)
            result["unique_auction_ids_in_page"] = len({
                o["auction_id"] for o in observations
            })
            result["counts_match"] = (
                len(observations) == int(reported_count)
                if reported_count is not None else None
            )
            result["parse_warnings"] = warnings

            # Keep each observation with its catalog-entry provenance.
            for observation in observations:
                observation["source_authority"] = catalog.get(
                    "source_authority", "Jaipur Development Authority"
                )
                observation["catalog_entry_number"] = index
                observation["catalog_scheme_name"] = scheme_name
                observation["catalog_reported_auction_count"] = reported_count
                all_observations.append(observation)
            progress.setdefault("entries", {})[listing_url] = {
                "status": "success",
                "http_status": response.status_code,
                "observations": observations,
                "counts_match": result["counts_match"],
            }

            if result["counts_match"] is False:
                result["error"] = (
                    f"Count mismatch: source reports {reported_count}, "
                    f"collector parsed {len(observations)} auction rows"
                )
                errors.append(result.copy())

            if warnings:
                errors.append(result.copy())

        except requests.RequestException as exc:
            result["error"] = f"Request error: {exc}"
            errors.append(result.copy())
        except Exception as exc:
            result["error"] = f"Parsing error: {type(exc).__name__}: {exc}"
            errors.append(result.copy())

        scheme_results.append(result)
        progress["updated_at_utc"] = now_utc()
        args.progress.parent.mkdir(parents=True, exist_ok=True)
        args.progress.write_text(json.dumps(progress, indent=2), encoding="utf-8")
        time.sleep(max(0.0, args.delay))

    id_counts = Counter(o["auction_id"] for o in all_observations)
    repeated_ids = {
        auction_id: count for auction_id, count in sorted(id_counts.items())
        if count > 1
    }

    deduped = {}
    for observation in all_observations:
        auction_id = str(observation["auction_id"])
        if auction_id not in deduped:
            deduped[auction_id] = dict(observation)
            deduped[auction_id]["source_provenance"] = []
        deduped[auction_id]["source_provenance"].append({
            "listing_url": observation.get("listing_url"),
            "catalog_scheme_name": observation.get("catalog_scheme_name"),
            "catalog_entry_number": observation.get("catalog_entry_number"),
        })

    output = {
        "source_authority": catalog.get(
            "source_authority", "Jaipur Development Authority"
        ),
        "source_summary_url": catalog.get("source_summary_url"),
        "retrieved_at_utc": now_utc(),
        "catalog_entries_attempted": len(entries),
        "scheme_pages_successful": sum(
            r["http_status"] == 200 and r["error"] is None
            for r in scheme_results
        ),
        "scheme_pages_with_errors_or_mismatches": len(errors),
        "observation_count": len(all_observations),
        "deduplicated_record_count": len(deduped),
        "unique_auction_id_count": len(id_counts),
        "repeated_auction_ids_across_observations": repeated_ids,
        "scheme_results": scheme_results,
        "observations": list(deduped.values()),
        "important_note": (
            "Read-only prototype output. Observations are intentionally not "
            "deduplicated. Portal listing does not establish current availability, "
            "eligibility, amendment status, or actionability. Verify official detail "
            "pages and corrigenda before treating an auction as actionable."
        ),
    }

    OUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ERROR_PATH.write_text(
        json.dumps(errors, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nCOLLECTION COMPLETE")
    print("Catalog entries:", len(entries))
    print("Scheme pages successful:", output["scheme_pages_successful"])
    print("Observations preserved:", len(all_observations))
    print("Unique auction IDs:", len(id_counts))
    print("Repeated IDs:", len(repeated_ids))
    print("Errors/mismatches:", len(errors))
    print("Observations:", OUT_PATH.resolve())
    print("Errors:", ERROR_PATH.resolve())
    print("SQLite database was not opened or modified.")


if __name__ == "__main__":
    main()
