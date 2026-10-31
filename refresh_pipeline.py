#!/usr/bin/env python3
"""Run official-source refreshes with validated optional JSON promotion.

Each run is written below data/refresh_pipeline/runs/<run_id>/. Promotion is
atomic and occurs only after a complete validated UDH catalogue response.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import collect_riico_staging as riico
from collector_jda_post_auction import parse_source
from refresh_jda_catalogue import parse_page

ROOT = Path(__file__).resolve().parent
PIPELINE_DIR = ROOT / "data" / "refresh_pipeline"
CACHE_DIR = PIPELINE_DIR / "cache"
RUNS_DIR = PIPELINE_DIR / "runs"
RIICO_CACHE_DIR = PIPELINE_DIR / "riico_cache"
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

JDA_POST_URL = "https://jdaservice.rajasthan.gov.in/jdamicroportalproject/NoticeBoard/PostAuction"
UDH_INDEX_URL = "https://udhonline.rajasthan.gov.in/Portal/AuctionListNew"
POST_AUCTION_FILE = ROOT / "data" / "jda_post_auction_latest.json"
CATALOGUE_FILE = ROOT / "deploy_data" / "auctions.json"
DISCOVERY_FILE = ROOT / "data" / "udh_test" / "jda_scheme_discovery.json"
REFRESH_LOCK_FILE = PIPELINE_DIR / "refresh.lock"


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


def validate_catalogue_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required = (
        "auction_id", "title", "scheme_name", "property_number",
        "property_area", "emd_end", "bid_end", "source_url",
    )
    if not records:
        raise ValueError("UDH catalogue response contained no records")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("UDH catalogue contains a non-object record")
        missing = [key for key in required if not str(record.get(key) or "").strip()]
        if missing:
            raise ValueError(
                f"UDH record {record.get('auction_id')!r} missing: {', '.join(missing)}"
            )
        auction_id = str(record["auction_id"]).strip()
        source_url = urlparse(str(record["source_url"]).strip())
        if source_url.scheme != "https" or source_url.netloc.lower() != "udhonline.rajasthan.gov.in":
            raise ValueError(f"UDH record {auction_id} has an unapproved source URL")
        for field in ("emd_end", "bid_end"):
            try:
                datetime.strptime(str(record[field]), "%d-%b-%Y %I:%M %p")
            except ValueError as exc:
                raise ValueError(
                    f"UDH record {auction_id} has invalid {field}: {record[field]!r}"
                ) from exc
    return records


@contextmanager
def refresh_lock():
    """Prevent overlapping button and scheduled refresh processes."""
    REFRESH_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = REFRESH_LOCK_FILE.open("a+", encoding="utf-8")
    try:
        try:
            import msvcrt
            handle.seek(0)
            handle.write("1")
            handle.flush()
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except ImportError:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (OSError, BlockingIOError) as exc:
        handle.close()
        raise RuntimeError("Another refresh is already running") from exc
    try:
        yield
    finally:
        unlock()
        handle.close()


def discover_udh_entries(
    session: requests.Session,
    *,
    retries: int,
    interval: float,
    cache_ttl: int,
    use_cache: bool,
) -> list[dict[str, Any]]:
    """Discover current UDH report and scheme listing links from the index."""
    index_body, _, _ = fetch(
        session, UDH_INDEX_URL, retries=retries, interval=interval,
        cache_ttl=cache_ttl, use_cache=use_cache,
    )
    index_soup = BeautifulSoup(index_body, "html.parser")
    report_urls = {
        urljoin(UDH_INDEX_URL, anchor["href"])
        for anchor in index_soup.find_all("a", href=True)
        if "/Portal/LiveAuctionDetailReport" in anchor["href"]
    }
    if not report_urls:
        raise ValueError("UDH index contains no live-auction report links")

    known_entries: dict[str, dict[str, Any]] = {}
    if DISCOVERY_FILE.exists():
        try:
            payload = json.loads(DISCOVERY_FILE.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("UDH discovery metadata is not an object")
            known_entries = {
                str(entry.get("official_listing_url")): entry
                for entry in payload.get("scheme_entries", [])
                if isinstance(entry, dict) and entry.get("official_listing_url")
            }
        except (OSError, ValueError, json.JSONDecodeError):
            known_entries = {}

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for report_url in sorted(report_urls):
        report_body, _, _ = fetch(
            session, report_url, retries=retries, interval=interval,
            cache_ttl=cache_ttl, use_cache=use_cache,
        )
        report_soup = BeautifulSoup(report_body, "html.parser")
        for anchor in report_soup.find_all("a", href=True):
            listing_url = urljoin(report_url, anchor["href"])
            parsed = urlparse(listing_url)
            if (
                parsed.scheme != "https"
                or parsed.netloc.lower() != "udhonline.rajasthan.gov.in"
                or "/Portal/PortalAuctionList" not in parsed.path
                or listing_url in seen
            ):
                continue
            seen.add(listing_url)
            row = anchor.find_parent("tr")
            row_text = " ".join((row or anchor).get_text(" ", strip=True).split())
            numbers = re.findall(r"\b\d+\b", row_text)
            known = known_entries.get(listing_url, {})
            entries.append({
                "report_url": report_url,
                "scheme_name": known.get("scheme_name") or row_text or listing_url,
                "reported_auction_count": known.get("reported_auction_count")
                if known.get("reported_auction_count") is not None
                else (int(numbers[-1]) if numbers else None),
                "official_listing_url": listing_url,
            })
    if not entries:
        raise ValueError("UDH reports contain no scheme listing links")
    return entries


def collect_udh_catalogue(
    session: requests.Session,
    *,
    retries: int,
    interval: float,
    cache_ttl: int,
    use_cache: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch every discovered official UDH scheme page, not just its IDs."""
    entries = discover_udh_entries(
        session, retries=retries, interval=interval,
        cache_ttl=cache_ttl, use_cache=use_cache,
    )

    records: list[dict[str, Any]] = []
    expected_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("UDH discovery contains an invalid scheme entry")
        url = str(entry.get("official_listing_url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "udhonline.rajasthan.gov.in":
            raise ValueError(f"UDH discovery contains an unapproved URL: {url}")
        expected = entry.get("reported_auction_count")
        if isinstance(expected, int):
            expected_count += expected
        body, _, _ = fetch(
            session, url, retries=retries, interval=interval,
            cache_ttl=cache_ttl, use_cache=use_cache,
        )
        page_records, _ = parse_page(
            body, url, entry, utc_now().isoformat(),
        )
        if not page_records:
            raise ValueError(f"UDH scheme returned no records: {entry.get('scheme_name')}")
        records.extend(page_records)

    records = validate_catalogue_records(records)
    if expected_count and len(records) != expected_count:
        raise ValueError(
            f"UDH catalogue is incomplete: parsed {len(records)}, expected {expected_count}"
        )
    return records, {
        "scheme_count": len(entries),
        "expected_record_count": expected_count,
        "record_count": len(records),
    }


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


def concise_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:240]}"


def _riico_cache_path(kind: str, unit_id: str) -> Path:
    return RIICO_CACHE_DIR / f"{kind}_{unit_id}.json"


def _read_riico_cache(kind: str, unit_id: str) -> list[dict[str, Any]] | dict[str, Any] | None:
    path = _riico_cache_path(kind, unit_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or "value" not in payload:
            return None
        return payload["value"]
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _write_riico_cache(kind: str, unit_id: str, value: Any, fetched_at: str) -> None:
    RIICO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _riico_cache_path(kind, unit_id)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"fetched_at": fetched_at, "value": value}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _mark_riico_stale(value: Any) -> Any:
    if isinstance(value, list):
        marked = []
        for record in value:
            row = dict(record)
            row["stale_data"] = True
            row["notes"] = (
                f"{row.get('notes', '').rstrip()} "
                "Refresh failed; cached official result retained and marked stale."
            ).strip()
            marked.append(row)
        return marked
    if isinstance(value, dict):
        result = dict(value)
        result["stale_data"] = True
        result["notes"] = "Refresh failed; cached official result retained and marked stale."
        return result
    return value


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
        "udh-jda-catalogue": "udh_catalogue",
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
        (
            "- Production promotion: **performed**"
            if manifest.get("promotion_performed")
            else "- Production promotion: **not performed**"
        ),
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


def promote_post_auction_records(records: list[dict[str, Any]]) -> int:
    """Atomically merge validated JDA outcomes into the app's production source."""
    existing: list[dict[str, Any]] = []
    if POST_AUCTION_FILE.exists():
        payload = json.loads(POST_AUCTION_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Existing JDA post-auction source is not a JSON list")
        existing = [record for record in payload if isinstance(record, dict)]

    merged: dict[str, dict[str, Any]] = {}
    for record in existing + records:
        fingerprint = str(record.get("record_fingerprint") or "").strip()
        if not fingerprint:
            raise ValueError("JDA post-auction record has no fingerprint")
        merged[fingerprint] = record

    POST_AUCTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f"{POST_AUCTION_FILE.name}.",
        suffix=".tmp",
        dir=POST_AUCTION_FILE.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(list(merged.values()), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, POST_AUCTION_FILE)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return len(merged)


def promote_catalogue_records(records: list[dict[str, Any]]) -> int:
    """Atomically replace the active catalogue after complete validation."""
    validated = validate_catalogue_records(records)
    historical: list[dict[str, Any]] = []
    if CATALOGUE_FILE.exists():
        payload = json.loads(CATALOGUE_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise ValueError("Existing UDH catalogue is not a valid records object")
        historical = [row for row in payload["records"] if isinstance(row, dict)]
    merged = historical + [
        row for row in validated
        if row not in historical
    ]
    CATALOGUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f"{CATALOGUE_FILE.name}.", suffix=".tmp", dir=CATALOGUE_FILE.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({
                "source_authority": "Jaipur Development Authority via Rajasthan UDH portal",
                "source_summary_url": UDH_INDEX_URL,
                "retrieved_at_utc": utc_now().isoformat(),
                "test_scope": "Complete discovered UDH catalogue",
                "catalogue_entries_attempted": len(validated),
                "parsed_record_count": len(merged),
                "records": merged,
            }, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, CATALOGUE_FILE)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return len(merged)


def collect_riico(
    session: requests.Session,
    checked: datetime,
    interval: float,
    *,
    retries: int,
    connect_timeout: float,
    read_timeout: float,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    result = source_result("riico-eauction", riico.AUCTION_URL, utc_now())
    records: list[dict[str, str]] = []
    zone_statuses: list[dict[str, Any]] = []
    for unit_id, unit_name in {**riico.UNITS, "53": "Dausa"}.items():
        last_error: Exception | None = None
        successful: list[dict[str, str]] | None = None
        for attempt in range(retries + 1):
            try:
                successful = riico.collect_unit(
                    unit_id, unit_name, checked, session=session,
                    timeout=(connect_timeout, read_timeout),
                )
                if not successful:
                    raise ValueError("empty response")
                _write_riico_cache("auction", unit_id, successful, checked.isoformat())
                break
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(min(30.0, 2**attempt) + random.uniform(0.0, 0.5))
        if successful is not None:
            records.extend(successful)
            zone_statuses.append({"unit": unit_name, "status": "success", "record_count": len(successful)})
        else:
            cached = _read_riico_cache("auction", unit_id)
            if isinstance(cached, list) and cached:
                stale = _mark_riico_stale(cached)
                records.extend(stale)
                zone_statuses.append({
                    "unit": unit_name, "status": "stale", "record_count": len(stale),
                    "error_summary": concise_error(last_error) if last_error else "refresh failed",
                })
            else:
                zone_statuses.append({
                    "unit": unit_name, "status": "failure", "record_count": 0,
                    "error_summary": concise_error(last_error) if last_error else "refresh failed",
                })
        if interval:
            time.sleep(interval)
    successful_zones = sum(status["status"] == "success" for status in zone_statuses)
    retained_zones = sum(status["status"] == "stale" for status in zone_statuses)
    failed_zones = len(zone_statuses) - successful_zones - retained_zones
    status = "success" if failed_zones == 0 and retained_zones == 0 else (
        "partial_success" if successful_zones or retained_zones else "failure"
    )
    summaries = [f"{item['unit']}: {item['error_summary']}" for item in zone_statuses if item.get("error_summary")]
    finish(result, status, len(records), "; ".join(summaries) or None)
    result.update({
        "units": len(zone_statuses),
        "successful_zones": successful_zones,
        "stale_zones": retained_zones,
        "failed_zones": failed_zones,
        "zone_statuses": zone_statuses,
        "error_summaries": summaries,
    })
    return records, result


def collect_direct(
    session: requests.Session,
    interval: float,
    *,
    retries: int,
    connect_timeout: float,
    read_timeout: float,
) -> dict[str, Any]:
    result = source_result("riico-direct-allotment", riico.DIRECT_URL, utc_now())
    checks: list[dict[str, str]] = []
    zone_statuses: list[dict[str, Any]] = []
    for unit_id, unit_name in {**riico.UNITS, "53": "Dausa"}.items():
        last_error: Exception | None = None
        successful: dict[str, str] | None = None
        for attempt in range(retries + 1):
            try:
                successful = riico.check_direct(
                    unit_id, unit_name, session=session,
                    timeout=(connect_timeout, read_timeout),
                )
                _write_riico_cache("direct", unit_id, successful, utc_now().isoformat())
                break
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(min(30.0, 2**attempt) + random.uniform(0.0, 0.5))
        if successful is not None:
            checks.append(successful)
            zone_statuses.append({"unit": unit_name, "status": "success"})
        else:
            cached = _read_riico_cache("direct", unit_id)
            if isinstance(cached, dict):
                checks.append(_mark_riico_stale(cached))
                zone_statuses.append({
                    "unit": unit_name, "status": "stale",
                    "error_summary": concise_error(last_error) if last_error else "refresh failed",
                })
            else:
                zone_statuses.append({
                    "unit": unit_name, "status": "failure",
                    "error_summary": concise_error(last_error) if last_error else "refresh failed",
                })
        if interval:
            time.sleep(interval)
    successful_zones = sum(item["status"] == "success" for item in zone_statuses)
    retained_zones = sum(item["status"] == "stale" for item in zone_statuses)
    failed_zones = len(zone_statuses) - successful_zones - retained_zones
    status = "success" if failed_zones == 0 and retained_zones == 0 else (
        "partial_success" if successful_zones or retained_zones else "failure"
    )
    summaries = [f"{item['unit']}: {item['error_summary']}" for item in zone_statuses if item.get("error_summary")]
    result["checks"] = checks
    finish(result, status, len(checks), "; ".join(summaries) or None)
    result.update({
        "units": len(zone_statuses),
        "successful_zones": successful_zones,
        "stale_zones": retained_zones,
        "failed_zones": failed_zones,
        "zone_statuses": zone_statuses,
        "error_summaries": summaries,
    })
    return result


def _run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    checked = datetime.now(IST).replace(microsecond=0)
    identifier = args.run_id or run_id()
    run_dir = RUNS_DIR / identifier
    run_dir.mkdir(parents=True, exist_ok=False)
    session = requests.Session()
    session.headers["User-Agent"] = "JaipurLandIntelligenceRefresh/1.0 (official-source research)"
    sources: list[dict[str, Any]] = []
    staged: dict[str, list[dict[str, Any]]] = {}

    riico_records, result = collect_riico(
        session, checked, args.interval, retries=args.riico_retries,
        connect_timeout=args.riico_connect_timeout,
        read_timeout=args.riico_read_timeout,
    )
    result["run_id"] = identifier
    result["new_count"], result["changed_count"], result["removed_count"] = compare_previous(
        result["source_key"], riico_records, result["fetch_status"]
    )
    sources.append(result)
    staged["riico_eauction"] = riico_records

    result = collect_direct(
        session, args.interval, retries=args.riico_retries,
        connect_timeout=args.riico_connect_timeout,
        read_timeout=args.riico_read_timeout,
    )
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

    jda_live_result = source_result("udh-jda-catalogue", UDH_INDEX_URL, utc_now())
    try:
        records, details = collect_udh_catalogue(
            session, retries=args.retries, interval=args.interval,
            cache_ttl=args.cache_ttl, use_cache=not args.no_cache,
        )
        staged["udh_catalogue"] = records
        status = "success"
        finish(jda_live_result, status, len(records))
        jda_live_result["new_count"], jda_live_result["changed_count"], jda_live_result["removed_count"] = compare_previous(
            jda_live_result["source_key"], records, status
        )
        jda_live_result.update(details)
    except (requests.RequestException, RuntimeError, ValueError, StopIteration) as exc:
        finish(jda_live_result, "failure", error=f"{type(exc).__name__}: {exc}")
    jda_live_result["run_id"] = identifier
    sources.append(jda_live_result)

    manifest = {
        "run_id": identifier,
        "checked_at": checked.isoformat(),
        "production_catalogue_untouched": True,
        "production_promotions": [],
        "promotion_performed": False,
        "sources": sources,
        "staged_files": {},
        "coverage_note": "Additional tenders, acquisitions, and non-JDA urban authorities are not implemented in this run.",
    }
    for key, records in staged.items():
        path = run_dir / f"{key}.json"
        path.write_text(json.dumps({"run_id": identifier, "records": records}, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest["staged_files"][key] = str(path.relative_to(ROOT))
    if getattr(args, "promote_jda_post_auction", False) and jda_result["fetch_status"] == "success":
        promoted_count = promote_post_auction_records(staged["jda_post_auction"])
        manifest["production_promotions"].append({
            "target": str(POST_AUCTION_FILE.relative_to(ROOT)),
            "source": "jda-post-auction",
            "record_count": promoted_count,
        })
        manifest["promotion_performed"] = True
        manifest["production_catalogue_untouched"] = True
    if getattr(args, "promote_udh_catalogue", False) and jda_live_result["fetch_status"] == "success":
        promoted_count = promote_catalogue_records(staged["udh_catalogue"])
        manifest["production_promotions"].append({
            "target": str(CATALOGUE_FILE.relative_to(ROOT)),
            "source": "udh-jda-catalogue",
            "record_count": promoted_count,
        })
        manifest["promotion_performed"] = True
        manifest["production_catalogue_untouched"] = False
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (PIPELINE_DIR / "latest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(manifest, run_dir / "report.md")
    write_report(manifest, PIPELINE_DIR / "latest_report.md")
    return manifest


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    with refresh_lock():
        return _run_pipeline(args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="Use a unique UTC run ID; default is generated.")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between official requests.")
    parser.add_argument("--cache-ttl", type=int, default=900)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--riico-retries", type=int, default=2)
    parser.add_argument("--riico-connect-timeout", type=float, default=10.0)
    parser.add_argument("--riico-read-timeout", type=float, default=30.0)
    parser.add_argument(
        "--promote-jda-post-auction",
        action="store_true",
        help="Merge successful JDA outcomes into data/jda_post_auction_latest.json.",
    )
    parser.add_argument(
        "--promote-udh-catalogue",
        action="store_true",
        help="Replace deploy_data/auctions.json after a complete UDH refresh.",
    )
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
