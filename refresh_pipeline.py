#!/usr/bin/env python3
"""Run the independent official-source refreshes into isolated run staging.

This command never promotes records into the production SQLite catalogue. Each
run is written below data/refresh_pipeline/runs/<run_id>/ and can be reviewed
before a separate import decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests
from bs4 import BeautifulSoup

import collect_riico_staging as riico
from collect_new_opportunities import collect_jda_live_ids
from collector_jda_post_auction import parse_source

ROOT = Path(__file__).resolve().parent
PIPELINE_DIR = ROOT / "data" / "refresh_pipeline"
CACHE_DIR = PIPELINE_DIR / "cache"
RUNS_DIR = PIPELINE_DIR / "runs"
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

JDA_POST_URL = "https://jdaservice.rajasthan.gov.in/jdamicroportalproject/NoticeBoard/PostAuction"
UDH_INDEX_URL = "https://udhonline.rajasthan.gov.in/Portal/AuctionListNew"


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def run_id(now: datetime | None = None) -> str:
    return (now or utc_now()).strftime("%Y%m%dT%H%M%SZ")


def cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def fetch(
    session: requests.Session,
    url: str,
    *,
    retries: int,
    interval: float,
    cache_ttl: int,
    use_cache: bool,
) -> tuple[str, int, bool]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    body_path = CACHE_DIR / f"{cache_key(url)}.body"
    meta_path = CACHE_DIR / f"{cache_key(url)}.json"
    if use_cache and body_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        age = utc_now().timestamp() - float(meta["fetched_at_epoch"])
        if age <= cache_ttl:
            return body_path.read_text(encoding="utf-8"), int(meta["status"]), True

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(min(120.0, 2**(attempt - 1)))
        try:
            response = session.get(url, timeout=60)
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    retry_seconds = min(120.0, max(0.0, float(retry_after)))
                except ValueError:
                    retry_seconds = 0.0
                if attempt < retries:
                    time.sleep(retry_seconds)
                response.raise_for_status()
            response.raise_for_status()
            if interval:
                time.sleep(interval)
            body_path.write_text(response.text, encoding="utf-8")
            meta_path.write_text(
                json.dumps(
                    {
                        "url": url,
                        "status": response.status_code,
                        "fetched_at": utc_now().isoformat(),
                        "fetched_at_epoch": utc_now().timestamp(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            return response.text, response.status_code, False
        except requests.RequestException as exc:
            last_error = exc
    raise RuntimeError(f"Fetch failed after {retries + 1} attempts: {last_error}")


def source_result(key: str, url: str, started: datetime) -> dict[str, Any]:
    return {
        "source_key": key,
        "source_url": url,
        "started_at": started.isoformat(),
        "finished_at": None,
        "last_success_at": None,
        "fetch_status": "not_started",
        "record_count": 0,
        "new_count": 0,
        "changed_count": 0,
        "error": None,
        "run_id": None,
    }


def finish(result: dict[str, Any], status: str, count: int = 0, error: str | None = None) -> None:
    now = utc_now().isoformat()
    result["finished_at"] = now
    result["fetch_status"] = status
    result["record_count"] = count
    result["error"] = error
    if status in {"success", "partial_success"}:
        result["last_success_at"] = now


def record_key(record: dict[str, Any]) -> str:
    return str(
        record.get("original_identifier")
        or record.get("auction_id")
        or record.get("record_fingerprint")
        or hashlib.sha256(
            json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    )


def comparable_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in {"last_checked_at", "retrieved_at_utc", "imported_at"}
    }


def compare_previous(
    source_key: str,
    records: list[dict[str, Any]],
    status: str,
) -> tuple[int, int, int]:
    if status not in {"success", "partial_success"}:
        return 0, 0, 0
    previous = PIPELINE_DIR / "latest.json"
    if not previous.exists():
        return len(records), 0, 0
    manifest = json.loads(previous.read_text(encoding="utf-8"))
    staged_name = {
        "riico-eauction": "riico_eauction",
        "jda-post-auction": "jda_post_auction",
        "udh-jda-live-auctions": "udh_jda_live_ids",
    }.get(source_key)
    old_path = manifest.get("staged_files", {}).get(staged_name)
    if not old_path:
        return len(records), 0, 0
    old_file = ROOT / old_path
    if not old_file.exists():
        return len(records), 0, 0
    old_records = json.loads(old_file.read_text(encoding="utf-8")).get("records", [])
    old_by_key = {record_key(row): row for row in old_records}
    new_by_key = {record_key(row): row for row in records}
    new_count = len(set(new_by_key) - set(old_by_key))
    changed_count = sum(
        comparable_record(old_by_key[key]) != comparable_record(new_by_key[key])
        for key in set(old_by_key) & set(new_by_key)
    )
    removed_count = len(set(old_by_key) - set(new_by_key))
    return new_count, changed_count, removed_count


def write_report(manifest: dict[str, Any], path: Path) -> None:
    lines = [
        "# Refresh ingestion report",
        "",
        f"- Run ID: `{manifest['run_id']}`",
        f"- Checked at: `{manifest['checked_at']}`",
        "- Production promotion: **not performed**",
        "",
        "| Source | Status | Records | New | Changed | Removed | Error |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for item in manifest["sources"]:
        lines.append(
            f"| `{item['source_key']}` | `{item['fetch_status']}` | "
            f"{item['record_count']} | {item.get('new_count', 0)} | "
            f"{item.get('changed_count', 0)} | {item.get('removed_count', 0)} | "
            f"{item.get('error') or ''} |"
        )
    lines.extend(
        [
            "",
            "Failed or rate-limited sources do not produce removals. A record is "
            "not considered cancelled merely because a fetch failed.",
            "",
            manifest["coverage_note"],
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_riico(session: requests.Session, checked: datetime, interval: float) -> tuple[list[dict[str, str]], dict[str, Any]]:
    result = source_result("riico-eauction", riico.AUCTION_URL, utc_now())
    records: list[dict[str, str]] = []
    errors: list[str] = []
    for unit_id, unit_name in {**riico.UNITS, "53": "Dausa"}.items():
        url = riico.AUCTION_URL + f"?unitId={unit_id}&viewBy=1&usageType=&DevelopmentStatus=0"
        try:
            records.extend(riico.collect_unit(unit_id, unit_name, checked, session=session))
            if interval:
                time.sleep(interval)
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            errors.append(f"{unit_id} {unit_name}: {type(exc).__name__}: {exc}")
    finish(result, "partial_success" if errors and records else "failure" if errors else "success", len(records), "; ".join(errors) or None)
    result["units"] = 5
    return records, result


def collect_direct(session: requests.Session, interval: float) -> dict[str, Any]:
    result = source_result("riico-direct-allotment", riico.DIRECT_URL, utc_now())
    checks: list[dict[str, str]] = []
    errors: list[str] = []
    for unit_id, unit_name in {**riico.UNITS, "53": "Dausa"}.items():
        try:
            checks.append(riico.check_direct(unit_id, unit_name, session=session))
            if interval:
                time.sleep(interval)
        except (requests.RequestException, RuntimeError) as exc:
            errors.append(f"{unit_id} {unit_name}: {type(exc).__name__}: {exc}")
    result["checks"] = checks
    finish(result, "partial_success" if errors and checks else "failure" if errors else "success", 0, "; ".join(errors) or None)
    return result


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    checked = datetime.now(IST).replace(microsecond=0)
    identifier = args.run_id or run_id()
    run_dir = RUNS_DIR / identifier
    run_dir.mkdir(parents=True, exist_ok=False)
    session = requests.Session()
    session.headers["User-Agent"] = "JaipurLandIntelligenceRefresh/1.0 (official-source research)"
    sources: list[dict[str, Any]] = []
    staged: dict[str, list[dict[str, Any]]] = {}

    riico_records, result = collect_riico(session, checked, args.interval)
    result["run_id"] = identifier
    result["new_count"], result["changed_count"], result["removed_count"] = compare_previous(
        result["source_key"], riico_records, result["fetch_status"]
    )
    sources.append(result)
    staged["riico_eauction"] = riico_records

    result = collect_direct(session, args.interval)
    result["run_id"] = identifier
    sources.append(result)

    jda_result = source_result("jda-post-auction", JDA_POST_URL, utc_now())
    try:
        body, status, cached = fetch(
            session, JDA_POST_URL, retries=args.retries, interval=args.interval,
            cache_ttl=args.cache_ttl, use_cache=not args.no_cache,
        )
        records = parse_source(body)
        staged["jda_post_auction"] = records
        jda_result["cached"] = cached
        finish(jda_result, "success", len(records))
        jda_result["new_count"], jda_result["changed_count"], jda_result["removed_count"] = compare_previous(
            jda_result["source_key"], records, jda_result["fetch_status"]
        )
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        finish(jda_result, "failure", error=f"{type(exc).__name__}: {exc}")
    jda_result["run_id"] = identifier
    sources.append(jda_result)

    jda_live_result = source_result("udh-jda-live-auctions", UDH_INDEX_URL, utc_now())
    try:
        ids, views, urls, limitation = collect_jda_live_ids(session)
        staged["udh_jda_live_ids"] = [{"auction_id": value} for value in sorted(ids)]
        status = "partial_success" if limitation else "success"
        finish(jda_live_result, status, len(ids), limitation or None)
        jda_live_result["new_count"], jda_live_result["changed_count"], jda_live_result["removed_count"] = compare_previous(
            jda_live_result["source_key"], staged["udh_jda_live_ids"], status
        )
        jda_live_result.update({"usage_views": views, "detail_urls": urls})
    except (requests.RequestException, RuntimeError, ValueError, StopIteration) as exc:
        finish(jda_live_result, "failure", error=f"{type(exc).__name__}: {exc}")
    jda_live_result["run_id"] = identifier
    sources.append(jda_live_result)

    manifest = {
        "run_id": identifier,
        "checked_at": checked.isoformat(),
        "production_catalogue_untouched": True,
        "promotion_performed": False,
        "sources": sources,
        "staged_files": {},
        "coverage_note": "Additional tenders, acquisitions, and non-JDA urban authorities are not implemented in this run.",
    }
    for key, records in staged.items():
        path = run_dir / f"{key}.json"
        path.write_text(json.dumps({"run_id": identifier, "records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest["staged_files"][key] = str(path.relative_to(ROOT))
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (PIPELINE_DIR / "latest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(manifest, run_dir / "report.md")
    write_report(manifest, PIPELINE_DIR / "latest_report.md")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="Use a unique UTC run ID; default is generated.")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between official requests.")
    parser.add_argument("--cache-ttl", type=int, default=900)
    parser.add_argument("--no-cache", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    result = run_pipeline(parse_args())
    print(json.dumps({
        "run_id": result["run_id"],
        "sources": [
            (item["source_key"], item["fetch_status"], item["record_count"])
            for item in result["sources"]
        ],
        "production_catalogue_untouched": result["production_catalogue_untouched"],
    }, indent=2))
