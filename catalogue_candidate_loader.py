"""Offline loading and presentation mapping for an explicitly selected run.

This module never refreshes sources or publishes data. It loads an existing
local run, delegates candidate validation to ``catalogue_publication``, and
maps validated records to the shape currently expected by ``app.load_catalogue``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from catalogue_publication import (
    ADAPTERS,
    NormalizedRecord,
    PublicationValidationError,
    normalize_candidate,
)

SOURCE_STAGE_KEYS = {
    "riico-eauction": "riico_eauction",
    "jda-post-auction": "jda_post_auction",
    "udh-jda-live-auctions": "udh_jda_live_ids",
}


@dataclass(frozen=True)
class LoadedCandidate:
    run_id: str
    manifest: Mapping[str, Any]
    source_records: Mapping[str, list[Mapping[str, Any]]]
    normalized_records: tuple[NormalizedRecord, ...]


def _error(message: str) -> PublicationValidationError:
    return PublicationValidationError(f"Offline candidate load failed: {message}")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise _error(f"missing file: {path}") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise _error(f"malformed JSON: {path}") from exc


def _resolve_staged_path(
    staged_value: Any, *, repository_root: Path, manifest_path: Path
) -> Path:
    if not isinstance(staged_value, str) or not staged_value.strip():
        raise _error("staged file path is missing")
    candidate = Path(staged_value)
    if candidate.is_absolute():
        raise _error("staged file path must be relative")
    root = repository_root.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise _error("staged file path escapes repository root") from exc
    return resolved


def _contained_path(path: Path, root: Path, description: str) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise _error(f"{description} escapes repository root") from exc
    return resolved_path


def _records_from_payload(
    payload: Any,
    path: Path,
    expected_run_id: str,
    expected_source_key: str,
) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        raise _error(f"staged payload is not an object: {path}")
    payload_run_id = payload.get("run_id")
    if not isinstance(payload_run_id, str) or not payload_run_id.strip():
        raise _error(f"staged payload is missing run_id: {path}")
    if payload_run_id != expected_run_id:
        raise _error(
            f"run_id mismatch between manifest and staged payload: {path}"
        )
    payload_source_key = payload.get("source_key")
    if payload_source_key != expected_source_key:
        raise _error(
            f"source_key mismatch between manifest and staged payload: {path}"
        )
    records = payload.get("records")
    if not isinstance(records, list) or not all(
        isinstance(record, Mapping) for record in records
    ):
        raise _error(f"staged payload has no object records list: {path}")
    return list(records)


def load_candidate(
    manifest_path: Path,
    *,
    repository_root: Path,
    as_of_utc: datetime,
) -> LoadedCandidate:
    """Load and validate one local run without invoking collection or publication."""
    repository_root = repository_root.resolve()
    manifest_path = _contained_path(
        manifest_path, repository_root, "manifest path"
    )
    payload = _read_json(manifest_path)
    if not isinstance(payload, Mapping):
        raise _error("manifest is not an object")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise _error("manifest is missing run_id")
    sources = payload.get("sources")
    staged_files = payload.get("staged_files")
    if not isinstance(sources, list) or not sources:
        raise _error("manifest has no sources")
    if not isinstance(staged_files, Mapping):
        raise _error("manifest has no staged_files mapping")

    source_records: dict[str, list[Mapping[str, Any]]] = {}
    source_keys: set[str] = set()
    staged_paths: set[Path] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            raise _error("manifest contains a malformed source entry")
        source_key = source.get("source_key")
        if not isinstance(source_key, str) or not source_key.strip():
            raise _error("manifest source is missing source_key")
        if source_key in source_keys:
            raise _error(f"manifest contains duplicate source_key: {source_key}")
        source_keys.add(source_key)
        if source_key not in ADAPTERS or source_key not in SOURCE_STAGE_KEYS:
            raise _error(f"unsupported source: {source_key!r}")
        if source.get("run_id") not in (None, run_id):
            raise _error(f"source run_id mismatch: {source_key}")
        stage_key = SOURCE_STAGE_KEYS[source_key]
        if stage_key not in staged_files:
            raise _error(f"manifest has no staged file for source: {source_key}")
        staged_path = _resolve_staged_path(
            staged_files[stage_key],
            repository_root=repository_root,
            manifest_path=manifest_path,
        )
        if staged_path in staged_paths:
            raise _error(f"staged file is reused by multiple sources: {staged_path}")
        staged_paths.add(staged_path)
        records = _records_from_payload(
            _read_json(staged_path), staged_path, run_id, source_key
        )
        source_records[source_key] = records

    normalized_run_id, normalized = normalize_candidate(
        payload, source_records, as_of_utc=as_of_utc
    )
    if normalized_run_id != run_id:
        raise _error("normalized candidate run_id differs from manifest")
    return LoadedCandidate(
        run_id=run_id,
        manifest=payload,
        source_records=source_records,
        normalized_records=tuple(normalized),
    )


def _presentation_category(land_use: str) -> tuple[str, str]:
    """Return the existing UI category shape without fabricating source fields."""
    lowered = land_use.casefold()
    if "residential" in lowered:
        return "residential", "Residential"
    if any(term in lowered for term in ("commercial", "shop", "office", "transport")):
        return "commercial", "Commercial / shop / transport"
    return "other", "Other / unclassified"


def _presentation_status_class(classification: str) -> str:
    if classification == "historical":
        return "past"
    if classification == "active" or classification == "upcoming":
        return "future"
    return "warn"


def to_homepage_record(record: NormalizedRecord) -> dict[str, Any]:
    """Map validated data to app.load_catalogue's record shape.

    No raw source field is invented. Fields absent from the normalized contract
    remain empty or have the same UI compatibility fallback used by app.py.
    """
    category, category_label = _presentation_category(record.land_use_category)
    return {
        "auction_id": record.canonical_id,
        "title": record.title,
        "scheme": record.scheme_or_area or "Not stated",
        "property_number": record.property_or_plot or "Not stated",
        "area": record.area or "Not stated",
        "usage": record.land_use_category or "Not stated",
        "emd": record.emd or "Not stated",
        "assessed_value": record.reserve_price_or_rate or "Not stated",
        "emd_start": record.application_start or "Not reliably extracted",
        "emd_end": record.application_end or "Not reliably extracted",
        "bid_start": record.bid_start or "Not reliably extracted",
        "bid_end": record.bid_end or "Not reliably extracted",
        "category": category,
        "category_label": category_label,
        "deadline_status": record.status,
        "status_class": _presentation_status_class(record.classification),
        "listing_links": [record.source_url],
        "detail_links": [],
        "provenance_count": 1,
        "raw_text": "",
        "source_authority": record.authority,
        "_bid_dt": None,
        "_price": None,
    }


def to_homepage_records(candidate: LoadedCandidate) -> list[dict[str, Any]]:
    return [to_homepage_record(record) for record in candidate.normalized_records]
