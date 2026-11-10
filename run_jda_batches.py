#!/usr/bin/env python3
"""Resume conservative, staging-only JDA live catalogue collection."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

import refresh_pipeline


ROOT = Path(__file__).resolve().parent
DISCOVERY_FILE = ROOT / "data" / "udh_test" / "jda_scheme_discovery.json"
PROGRESS_FILE = ROOT / "data" / "udh_test" / "jda_live_collection_progress.json"
IST = timezone(timedelta(hours=5, minutes=30))
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JaipurLandIntelligence/0.1)"}
DATE_FORMAT = "%d-%b-%Y %I:%M %p"


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def current_entries(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for entry in discovery.get("scheme_entries", []):
        if (
            isinstance(entry, dict)
            and entry.get("discovery_status", "current") == "current"
        ):
            entries.append(entry)
    return entries


def successful_count(progress: dict[str, Any]) -> int:
    return sum(
        isinstance(value, dict) and value.get("status") == "success"
        for value in progress.get("entries", {}).values()
    )


def select_entries(
    discovery: dict[str, Any],
    progress: dict[str, Any],
    *,
    pages: int | None,
    remaining: bool,
) -> list[dict[str, Any]]:
    if pages is not None and pages < 1:
        raise ValueError("--pages must be at least 1")
    completed = progress.get("entries", {})
    selected = []
    terminal_statuses = {"success", "failed", "rate_limited"}
    for entry in current_entries(discovery):
        url = str(entry.get("official_listing_url") or "").strip()
        saved = completed.get(url)
        if isinstance(saved, dict) and saved.get("status") in terminal_statuses:
            continue
        selected.append(entry)
        if pages is not None and len(selected) >= pages:
            break
    if not remaining and pages is None:
        return selected[:20]
    return selected


def _checkpoint(progress: dict[str, Any], path: Path) -> None:
    progress["updated_at_utc"] = refresh_pipeline.utc_now().isoformat()
    progress["completed_pages"] = successful_count(progress)
    refresh_pipeline.atomic_json_write(path, progress)


def _parse_deadline(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, DATE_FORMAT).replace(tzinfo=IST)
    except ValueError:
        return None


def classify_records(records: list[dict[str, Any]], now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(IST)
    unique = {str(record.get("auction_id")): record for record in records}
    counts = {"upcoming": 0, "expired": 0, "unverified": 0}
    for record in unique.values():
        emd_end = _parse_deadline(record.get("emd_end"))
        bid_end = _parse_deadline(record.get("bid_end"))
        if emd_end is None or bid_end is None:
            counts["unverified"] += 1
        elif bid_end <= now:
            counts["expired"] += 1
        elif emd_end <= now:
            counts["unverified"] += 1
        else:
            counts["upcoming"] += 1
    return counts


def cumulative_records(progress: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for result in progress.get("entries", {}).values():
        if isinstance(result, dict) and result.get("status") == "success":
            records.extend(
                record for record in result.get("records", [])
                if isinstance(record, dict)
            )
    return list({
        str(record.get("auction_id")): record
        for record in records
    }.values())


def collect(
    *,
    pages: int | None = None,
    remaining: bool = False,
    dry_run: bool = False,
    discovery_file: Path = DISCOVERY_FILE,
    progress_file: Path = PROGRESS_FILE,
) -> dict[str, Any]:
    discovery = load_json(discovery_file, {})
    progress = load_json(progress_file, {"entries": {}, "updated_at_utc": None})
    progress.setdefault("entries", {})
    selected = select_entries(
        discovery, progress, pages=pages, remaining=remaining,
    )
    before_success = successful_count(progress)
    summary = {
        "selected_pages": len(selected),
        "successful_pages": 0,
        "raw_records": 0,
        "unique_records": 0,
        "stopped": None,
    }
    if dry_run:
        summary["successful_pages_cumulative"] = before_success
        records = cumulative_records(progress)
        summary["unique_records"] = len(records)
        summary.update(classify_records(records))
        summary["failed_pages"] = sum(
            isinstance(value, dict) and value.get("status") == "failed"
            for value in progress["entries"].values()
        )
        summary["rate_limited_pages"] = sum(
            isinstance(value, dict) and value.get("status") == "rate_limited"
            for value in progress["entries"].values()
        )
        print(json.dumps(summary, indent=2))
        return summary

    session = requests.Session()
    session.headers.update(HEADERS)
    for entry in selected:
        url = str(entry.get("official_listing_url") or "").strip()
        try:
            body, status, _ = refresh_pipeline.fetch(
                session,
                url,
                retries=0,
                interval=1.0,
                cache_ttl=0,
                use_cache=False,
            )
            records, _ = refresh_pipeline.parse_page(
                body, url, entry, refresh_pipeline.utc_now().isoformat(),
            )
            records = refresh_pipeline.validate_catalogue_records(records)
            if not records:
                raise ValueError(f"UDH listing returned no records: {url}")
            progress["entries"][url] = {
                "status": "success",
                "http_status": status,
                "record_count": len(records),
                "records": records,
                "retrieved_at_utc": refresh_pipeline.utc_now().isoformat(),
            }
            _checkpoint(progress, progress_file)
            summary["successful_pages"] += 1
            summary["raw_records"] += len(records)
            print(
                f"Collected {summary['successful_pages']}/{len(selected)} pages: "
                f"{len(records)} records"
            )
        except refresh_pipeline.RateLimitedError as exc:
            progress["entries"][url] = {
                "status": "rate_limited",
                "retry_after": exc.retry_after,
                "updated_at_utc": refresh_pipeline.utc_now().isoformat(),
            }
            _checkpoint(progress, progress_file)
            summary["stopped"] = "HTTP 429"
            break
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            progress["entries"][url] = {
                "status": "failed",
                "error_type": type(exc).__name__,
                "updated_at_utc": refresh_pipeline.utc_now().isoformat(),
            }
            _checkpoint(progress, progress_file)
            summary["stopped"] = type(exc).__name__
            break

    records = cumulative_records(progress)
    summary["successful_pages_cumulative"] = successful_count(progress)
    summary["unique_records"] = len(records)
    summary.update(classify_records(records))
    summary["failed_pages"] = sum(
        isinstance(value, dict) and value.get("status") == "failed"
        for value in progress["entries"].values()
    )
    summary["rate_limited_pages"] = sum(
        isinstance(value, dict) and value.get("status") == "rate_limited"
        for value in progress["entries"].values()
    )
    print(json.dumps(summary, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--pages", type=int, help="Collect at most N new pages.")
    group.add_argument(
        "--remaining",
        action="store_true",
        help="Collect all current URLs not in a terminal checkpoint state.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Select and report pages without making network requests.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    result = collect(
        pages=arguments.pages,
        remaining=arguments.remaining,
        dry_run=arguments.dry_run,
    )
    if result.get("stopped"):
        raise SystemExit(2)
