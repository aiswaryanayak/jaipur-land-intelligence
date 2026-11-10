#!/usr/bin/env python3
"""Safely resume the isolated JDA fresh revalidation checkpoint."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import requests

import refresh_pipeline


ROOT = Path(__file__).resolve().parent
DISCOVERY_FILE = ROOT / "data" / "udh_test" / "jda_scheme_discovery.json"
DEFAULT_CHECKPOINT = (
    ROOT / "data" / "udh_test"
    / "jda_fresh_revalidation_progress_20261110T154814Z.json"
)
ORIGINAL_CHECKPOINT = ROOT / "data" / "udh_test" / "jda_live_collection_progress.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JaipurLandIntelligence/0.1)"}
MAX_PAGES = 20


def load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def validate_checkpoint_path(path: Path) -> None:
    if path.resolve() == ORIGINAL_CHECKPOINT.resolve():
        raise ValueError("The original completed checkpoint cannot be used for resume")
    if "jda_fresh_revalidation_progress_" not in path.name:
        raise ValueError("Resume requires a timestamped fresh revalidation checkpoint")


def current_entries(discovery: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for entry in discovery.get("scheme_entries", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("discovery_status", "current") != "current":
            continue
        url = str(entry.get("official_listing_url") or "").strip()
        if url:
            result[url] = entry
    return result


def selected_urls(
    discovery: dict[str, Any],
    progress: dict[str, Any],
    limit: int = MAX_PAGES,
) -> list[str]:
    if limit < 1 or limit > MAX_PAGES:
        raise ValueError(f"limit must be between 1 and {MAX_PAGES}")
    entries = current_entries(discovery)
    saved = progress.get("entries", {})
    failed = [
        url for url, value in saved.items()
        if isinstance(value, dict)
        and value.get("status") == "failed"
        and url in entries
    ]
    pending = [
        url for url in entries
        if not isinstance(saved.get(url), dict)
        or saved[url].get("status") == "pending"
    ]
    return (failed[:1] + pending)[:limit]


def checkpoint(progress: dict[str, Any], path: Path) -> None:
    progress["updated_at_utc"] = refresh_pipeline.utc_now().isoformat()
    progress["completed_pages"] = sum(
        isinstance(value, dict) and value.get("status") == "success"
        for value in progress.get("entries", {}).values()
    )
    refresh_pipeline.atomic_json_write(path, progress)


def resume(
    *,
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    discovery_path: Path = DISCOVERY_FILE,
    limit: int = MAX_PAGES,
) -> dict[str, Any]:
    validate_checkpoint_path(checkpoint_path)
    discovery = load_object(discovery_path)
    progress = load_object(checkpoint_path)
    if not isinstance(progress.get("entries"), dict):
        raise ValueError("Fresh revalidation checkpoint has no entries map")
    selected = selected_urls(discovery, progress, limit)
    summary = {
        "selected_pages": len(selected),
        "successful_pages": 0,
        "raw_records": 0,
        "stopped": None,
        "checkpoint": str(checkpoint_path),
    }
    session = requests.Session()
    session.headers.update(HEADERS)
    entries = current_entries(discovery)

    for url in selected:
        entry = entries[url]
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
                "scheme_name": entry.get("scheme_name"),
            }
            checkpoint(progress, checkpoint_path)
            summary["successful_pages"] += 1
            summary["raw_records"] += len(records)
        except refresh_pipeline.RateLimitedError as exc:
            progress["entries"][url] = {
                "status": "rate_limited",
                "retry_after": exc.retry_after,
                "updated_at_utc": refresh_pipeline.utc_now().isoformat(),
                "scheme_name": entry.get("scheme_name"),
            }
            checkpoint(progress, checkpoint_path)
            summary["stopped"] = "HTTP 429"
            break
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            progress["entries"][url] = {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "updated_at_utc": refresh_pipeline.utc_now().isoformat(),
                "scheme_name": entry.get("scheme_name"),
            }
            checkpoint(progress, checkpoint_path)
            summary["stopped"] = type(exc).__name__
            break

    summary["successful_pages_cumulative"] = sum(
        isinstance(value, dict) and value.get("status") == "success"
        for value in progress["entries"].values()
    )
    summary["failed_pages"] = sum(
        isinstance(value, dict) and value.get("status") == "failed"
        for value in progress["entries"].values()
    )
    summary["rate_limited_pages"] = sum(
        isinstance(value, dict) and value.get("status") == "rate_limited"
        for value in progress["entries"].values()
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--discovery", type=Path, default=DISCOVERY_FILE)
    parser.add_argument("--pages", type=int, default=MAX_PAGES)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    print(json.dumps(resume(
        checkpoint_path=args.checkpoint,
        discovery_path=args.discovery,
        limit=args.pages,
    ), indent=2))
