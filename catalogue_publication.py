"""Validate and atomically publish a reviewed catalogue candidate.

This module is deliberately separate from refresh collection and SQLite
promotion. Refresh manifests remain operational metadata; publication accepts
only an explicitly complete, reviewed candidate.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

APPROVED_REVIEW_STATUSES = frozenset({"approved_for_catalogue"})
VALID_CLASSIFICATIONS = frozenset({"active", "upcoming", "historical", "notice"})
AUTHORITY_NAMES = {
    "jda": "Jaipur Development Authority",
    "riico": "RIICO",
}
STABLE_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]*:[A-Za-z0-9][A-Za-z0-9._/-]*$")
SOURCE_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


class PublicationValidationError(ValueError):
    """Raised when a candidate is not safe to publish."""


@dataclass(frozen=True)
class NormalizedRecord:
    canonical_id: str
    authority: str
    source_url: str
    stable_source_id: str
    source_type: str
    title: str
    scheme_or_area: str
    property_or_plot: str
    land_use_category: str
    area: str
    reserve_price_or_rate: str
    emd: str
    application_start: str
    application_end: str
    bid_start: str
    bid_end: str
    status: str
    classification: str
    review_status: str
    observed_at: str
    notes: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _required_text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise PublicationValidationError(f"Missing required field: {field}")
    return result


def _timestamp(value: Any, field: str, *, required: bool = True) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise PublicationValidationError(f"Missing required date: {field}")
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%d-%b-%Y %I:%M %p").replace(
                tzinfo=SOURCE_TIMEZONE
            )
        except ValueError as exc:
            raise PublicationValidationError(f"Invalid date for {field}: {text!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PublicationValidationError(f"Naive date is not allowed for {field}")
    return parsed.astimezone(timezone.utc)


def _as_of(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise PublicationValidationError("as_of_utc must be timezone-aware")
    return value.astimezone(timezone.utc)


def _source_url(value: Any) -> str:
    url = _required_text(value, "source_url")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise PublicationValidationError(f"Invalid source URL: {url!r}")
    return url


def _authority_key(value: Any) -> str:
    authority = _required_text(value, "authority").casefold()
    if authority in {"jda", "jaipur development authority"}:
        return "jda"
    if authority == "riico":
        return "riico"
    raise PublicationValidationError(f"Unsupported authority: {value!r}")


def _qualified_id(authority: str, source_id: Any) -> tuple[str, str]:
    stable_id = _required_text(source_id, "stable_source_id")
    prefix = authority.upper()
    canonical_id = f"{prefix}:{stable_id}"
    if not STABLE_ID_PATTERN.fullmatch(canonical_id):
        raise PublicationValidationError(
            f"Invalid authority-qualified stable ID: {canonical_id!r}"
        )
    return canonical_id, stable_id


def _classification(status: Any, source_type: Any) -> str:
    status_text = _required_text(status, "status").casefold()
    source_text = str(source_type or "").casefold()
    historical_statuses = {
        "closed",
        "completed",
        "cancelled",
        "canceled",
        "expired",
    }
    historical_source_types = {
        "historical",
        "post_auction",
        "post-auction",
        "post_auction_result",
    }
    if (
        status_text in historical_statuses
        or (status_text.startswith("closed (") and status_text.endswith(")"))
        or source_text in historical_source_types
    ):
        return "historical"
    if status_text == "notice" or source_text == "notice":
        return "notice"
    if status_text == "upcoming":
        return "upcoming"
    if status_text in {"active", "live", "open"}:
        return "active"
    raise PublicationValidationError(
        f"Cannot classify record status safely: {status!r}"
    )


def _review_status(record: Mapping[str, Any]) -> str:
    review_status = _required_text(record.get("review_status"), "review_status")
    if review_status not in APPROVED_REVIEW_STATUSES:
        raise PublicationValidationError(
            f"Record is not approved for publication: {review_status!r}"
        )
    return review_status


def _record(
    record: Mapping[str, Any],
    *,
    authority: str,
    source_id: Any,
    source_type: Any,
    title: Any,
    scheme_or_area: Any,
    property_or_plot: Any,
    land_use_category: Any,
    area: Any,
    reserve_price_or_rate: Any,
    emd: Any,
    application_start: Any,
    application_end: Any,
    bid_start: Any,
    bid_end: Any,
    status: Any,
    observed_at: Any,
    notes: Any,
    as_of_utc: datetime,
) -> NormalizedRecord:
    canonical_id, stable_id = _qualified_id(authority, source_id)
    source_type_text = _required_text(source_type, "source_type")
    status_text = _required_text(status, "status")
    classification = _classification(status_text, source_type_text)
    if classification not in VALID_CLASSIFICATIONS:
        raise PublicationValidationError(
            f"Invalid record classification: {classification!r}"
        )
    for field, value in (
        ("application_start", application_start),
        ("application_end", application_end),
        ("bid_start", bid_start),
        ("bid_end", bid_end),
        ("observed_at", observed_at),
    ):
        _timestamp(value, field)
    closing_at = _timestamp(bid_end, "bid_end", required=False)
    if classification in {"active", "upcoming"}:
        if closing_at is None:
            raise PublicationValidationError(
                f"Active/upcoming record has no reliable bid_end: {source_id!r}"
            )
        if closing_at <= as_of_utc:
            classification = "historical"
            status_text = "expired"
    return NormalizedRecord(
        canonical_id=canonical_id,
        authority=AUTHORITY_NAMES[authority],
        source_url=_source_url(record.get("source_url")),
        stable_source_id=stable_id,
        source_type=source_type_text,
        title=_required_text(title, "title"),
        scheme_or_area=str(scheme_or_area or "").strip(),
        property_or_plot=str(property_or_plot or "").strip(),
        land_use_category=str(land_use_category or "").strip(),
        area=str(area or "").strip(),
        reserve_price_or_rate=str(reserve_price_or_rate or "").strip(),
        emd=str(emd or "").strip(),
        application_start=str(application_start or "").strip(),
        application_end=str(application_end or "").strip(),
        bid_start=str(bid_start or "").strip(),
        bid_end=str(bid_end or "").strip(),
        status=status_text,
        classification=classification,
        review_status=_review_status(record),
        observed_at=_required_text(observed_at, "observed_at"),
        notes=str(notes or "").strip(),
    )


def normalize_jda_record(
    record: Mapping[str, Any], *, as_of_utc: datetime | None = None
) -> NormalizedRecord:
    """Normalize a JDA refresh record without filling absent source fields."""
    return _record(
        record,
        authority=_authority_key(record.get("source_authority", "JDA")),
        source_id=record.get("auction_id") or record.get("record_fingerprint"),
        source_type=record.get("source_record_type") or "auction_listing",
        title=record.get("title"),
        scheme_or_area=record.get("scheme_name") or record.get("catalog_scheme_name"),
        property_or_plot=record.get("property_number") or record.get("plot_number"),
        land_use_category=record.get("usage_type") or record.get("property_category"),
        area=record.get("property_area") or record.get("area_sqm"),
        reserve_price_or_rate=record.get("assessed_value") or record.get("auctioned_rate_inr_per_sqm"),
        emd=record.get("emd_amount"),
        application_start=record.get("emd_start"),
        application_end=record.get("emd_end"),
        bid_start=record.get("bid_start") or record.get("auction_date"),
        bid_end=record.get("bid_end") or record.get("auction_date"),
        status=record.get("status") or record.get("current_status"),
        observed_at=record.get("retrieved_at_utc") or record.get("observed_at_utc"),
        notes=record.get("notes"),
        as_of_utc=_as_of(as_of_utc),
    )


def normalize_riico_record(
    record: Mapping[str, Any], *, as_of_utc: datetime | None = None
) -> NormalizedRecord:
    """Normalize one RIICO staging record without adapting it to promotion schema."""
    return _record(
        record,
        authority=_authority_key(record.get("authority", "RIICO")),
        source_id=record.get("original_identifier") or record.get("record_key"),
        source_type=record.get("source_type"),
        title=record.get("title"),
        scheme_or_area=record.get("scheme_or_industrial_area"),
        property_or_plot=record.get("property_or_plot_number"),
        land_use_category=record.get("land_use_category"),
        area=record.get("area"),
        reserve_price_or_rate=record.get("reserve_price_or_rate"),
        emd=record.get("emd"),
        application_start=record.get("application_start"),
        application_end=record.get("application_end"),
        bid_start=record.get("bid_start"),
        bid_end=record.get("bid_end"),
        status=record.get("current_status"),
        observed_at=record.get("last_checked_at"),
        notes=record.get("notes"),
        as_of_utc=_as_of(as_of_utc),
    )


ADAPTERS: dict[
    str, Callable[..., NormalizedRecord]
] = {
    "jda-post-auction": normalize_jda_record,
    "udh-jda-live-auctions": normalize_jda_record,
    "riico-eauction": normalize_riico_record,
}


def _validate_manifest(
    manifest: Mapping[str, Any],
    source_records: Mapping[str, Sequence[Mapping[str, Any]]],
) -> str:
    run_id = _required_text(manifest.get("run_id"), "manifest.run_id")
    if manifest.get("promotion_performed") is True:
        raise PublicationValidationError("Candidate manifest claims promotion was performed")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise PublicationValidationError("Manifest has no source results")
    by_key = {str(item.get("source_key")): item for item in sources if isinstance(item, Mapping)}
    if set(source_records) != set(by_key):
        raise PublicationValidationError(
            "Candidate source keys do not exactly match manifest source keys"
        )
    for source_key, records in source_records.items():
        result = by_key[source_key]
        if source_key not in ADAPTERS:
            raise PublicationValidationError(f"Unsupported publication source: {source_key}")
        if result.get("fetch_status") != "success" or result.get("error"):
            raise PublicationValidationError(
                f"Source {source_key} is not a successful refresh"
            )
        if result.get("coverage_complete") is not True:
            raise PublicationValidationError(
                f"Source {source_key} lacks explicit coverage_complete=true"
            )
        _validate_coverage_evidence(source_key, result, len(records))
        if result.get("record_count") != len(records):
            raise PublicationValidationError(
                f"Source {source_key} record count does not match manifest"
            )
        if not records:
            raise PublicationValidationError(
                f"Source {source_key} has no records to publish"
            )
    return run_id


def _validate_coverage_evidence(
    source_key: str, result: Mapping[str, Any], record_count: int
) -> None:
    """Reconcile collector-supplied evidence; this is not independent proof."""
    evidence = result.get("coverage_evidence")
    if not isinstance(evidence, Mapping):
        raise PublicationValidationError(
            f"Source {source_key} lacks source-specific coverage evidence"
        )
    if evidence.get("all_scope_checked") is not True:
        raise PublicationValidationError(
            f"Source {source_key} coverage scope was not fully checked"
        )
    if evidence.get("observed_record_count") != record_count:
        raise PublicationValidationError(
            f"Source {source_key} coverage count does not match records"
        )
    if source_key == "riico-eauction":
        unit_counts = evidence.get("unit_record_counts")
        if not isinstance(unit_counts, Mapping) or not unit_counts:
            raise PublicationValidationError(
                "RIICO coverage evidence lacks per-unit record counts"
            )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in unit_counts.values()
        ):
            raise PublicationValidationError(
                "RIICO per-unit counts must be non-negative integers"
            )
        if sum(unit_counts.values()) != record_count:
            raise PublicationValidationError(
                "RIICO per-unit counts contradict record_count"
            )
    elif source_key == "jda-post-auction":
        page_counts = evidence.get("page_record_counts")
        if (
            not isinstance(page_counts, list)
            or not page_counts
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in page_counts
            )
        ):
            raise PublicationValidationError(
                "JDA coverage evidence lacks page record counts"
            )
        if sum(page_counts) != record_count:
            raise PublicationValidationError(
                "JDA page counts contradict record_count"
            )
    elif source_key == "udh-jda-live-auctions":
        if evidence.get("listed_id_count") != record_count:
            raise PublicationValidationError(
                "UDH listed ID count contradicts record_count"
            )
        if evidence.get("detail_record_count") != record_count:
            raise PublicationValidationError(
                "UDH detail count contradicts record_count"
            )
        if evidence.get("detail_errors"):
            raise PublicationValidationError(
                "UDH coverage evidence reports detail errors"
            )


def normalize_candidate(
    manifest: Mapping[str, Any],
    source_records: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    as_of_utc: datetime | None = None,
) -> tuple[str, list[NormalizedRecord]]:
    """Validate refresh completeness and normalize each source explicitly."""
    run_id = _validate_manifest(manifest, source_records)
    normalized: list[NormalizedRecord] = []
    effective_as_of = _as_of(as_of_utc)
    for source_key, records in source_records.items():
        adapter = ADAPTERS[source_key]
        normalized.extend(
            adapter(record, as_of_utc=effective_as_of) for record in records
        )
    canonical_ids = [record.canonical_id for record in normalized]
    if len(canonical_ids) != len(set(canonical_ids)):
        raise PublicationValidationError("Duplicate canonical IDs in publication candidate")
    return run_id, normalized


def _load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PublicationValidationError(
            f"Existing publication is unreadable: {path}"
        ) from exc
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise PublicationValidationError("Existing publication has no records list")
    ids = [record.get("canonical_id") for record in records if isinstance(record, Mapping)]
    if len(ids) != len(records) or any(not value for value in ids):
        raise PublicationValidationError("Existing publication contains invalid records")
    if len(ids) != len(set(ids)):
        raise PublicationValidationError("Existing publication contains duplicate IDs")
    for record in records:
        _timestamp(record.get("observed_at"), "existing.observed_at")
    return [dict(record) for record in records]


def _age_existing(
    record: Mapping[str, Any], as_of_utc: datetime
) -> dict[str, Any]:
    aged = dict(record)
    if record.get("classification") == "active":
        closing_at = _timestamp(record.get("bid_end"), "existing.bid_end")
        if closing_at <= as_of_utc:
            aged["status"] = "expired"
            aged["classification"] = "historical"
    return aged


def publish(
    manifest: Mapping[str, Any],
    source_records: Mapping[str, Sequence[Mapping[str, Any]]],
    destination: Path,
    *,
    as_of_utc: datetime | None = None,
) -> dict[str, Any]:
    """Atomically publish a candidate, retaining absent records.

    Invalid existing records fail closed and remain unchanged; this function
    does not sanitize the previous publication after validation failure.
    """
    effective_as_of = _as_of(as_of_utc)
    run_id, normalized = normalize_candidate(
        manifest, source_records, as_of_utc=effective_as_of
    )
    existing = [
        _age_existing(record, effective_as_of)
        for record in _load_existing(destination)
    ]
    merged = {record["canonical_id"]: record for record in existing}
    existing_by_id = merged.copy()
    for record in normalized:
        current = existing_by_id.get(record.canonical_id)
        if current is None:
            merged[record.canonical_id] = record.as_dict()
            continue
        incoming_time = _timestamp(record.observed_at, "observed_at")
        current_time = _timestamp(current["observed_at"], "existing.observed_at")
        if incoming_time > current_time:
            merged[record.canonical_id] = record.as_dict()
        elif (
            incoming_time == current_time
            and current.get("classification") not in {"historical", "notice"}
        ):
            merged[record.canonical_id] = record.as_dict()
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "published_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "records": list(merged.values()),
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except (OSError, TypeError, ValueError) as exc:
        raise PublicationValidationError(
            f"Publication failed; existing catalogue was retained: {destination}"
        ) from exc
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass

    return {
        "run_id": run_id,
        "published_count": len(merged),
        "new_or_replaced_count": len(normalized),
        "retained_absent_count": len(existing) - len(
            set(record["canonical_id"] for record in existing)
            & set(record.canonical_id for record in normalized)
        ),
        "destination": str(destination),
    }
