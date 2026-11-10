#!/usr/bin/env python3
"""Prepare a flat, staging-only JDA catalogue preview for app.py."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
STAGING_DIR = ROOT / "data" / "authority_staging"
PRODUCTION_FILE = ROOT / "deploy_data" / "auctions.json"
STAMP = "20261110T204143Z"
CANDIDATES_FILE = STAGING_DIR / "jda_live_candidates_20261110T203450Z.json"
RESTRICTED_FILE = STAGING_DIR / "jda_live_restricted_unverified_20261110T203450Z.json"
MANIFEST_FILE = STAGING_DIR / "jda_live_staging_manifest_20261110T203450Z.json"

SQUARE_FEET_TO_SQUARE_METRES = 0.09290304
DATE_FIELDS = ("emd_start", "emd_end", "bid_start", "bid_end")
FLAT_REQUIRED_FIELDS = (
    "auction_id",
    "title",
    "scheme_name",
    "property_number",
    "property_area",
    "assessed_value",
    "emd_end",
    "bid_end",
    "source_url",
)


def load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def clean(value: Any) -> str:
    return " ".join(str(value).split()).strip() if value is not None else ""


def area_metadata(area: dict[str, Any]) -> dict[str, Any]:
    original = clean(area.get("original"))
    normalized = area.get("normalized")
    if not isinstance(normalized, dict):
        raise ValueError("Area normalized metadata is missing")
    value = normalized.get("value")
    unit = clean(normalized.get("unit")).casefold()
    if not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"Invalid area value: {original!r}")
    if unit in {"square meter", "square metres", "square metre", "sqm"}:
        square_metres = float(value)
    elif unit in {"square feet", "square foot", "sq ft", "sqft"}:
        square_metres = float(value) * SQUARE_FEET_TO_SQUARE_METRES
    else:
        raise ValueError(f"Unsupported area unit: {unit!r}")
    return {
        "original": original,
        "unit": normalized.get("unit"),
        "value": float(value),
        "square_metre_value": round(square_metres, 6),
        "conversion_factor": (
            1.0 if square_metres == float(value)
            else SQUARE_FEET_TO_SQUARE_METRES
        ),
    }


def provenance_for(auction_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    provenance = manifest.get("provenance", {}).get(auction_id)
    if not isinstance(provenance, dict):
        raise ValueError(f"Missing provenance for auction {auction_id}")
    listing_links = sorted({
        clean(url) for url in provenance.get("listing_urls", [])
        if clean(url)
    })
    detail_links = sorted({
        clean(url)
        for key in ("detail_urls", "report_urls")
        for url in provenance.get(key, [])
        if clean(url)
    })
    source_urls = sorted({
        clean(url) for url in provenance.get("source_urls", [])
        if clean(url)
    })
    if not listing_links or not source_urls:
        raise ValueError(f"Incomplete provenance for auction {auction_id}")
    return {
        "listing_links": listing_links,
        "detail_links": detail_links,
        "source_provenance": {
            "auction_id": auction_id,
            "observation_count": provenance.get("observation_count"),
            "listing_urls": listing_links,
            "detail_urls": sorted({
                clean(url) for url in provenance.get("detail_urls", []) if clean(url)
            }),
            "report_urls": sorted({
                clean(url) for url in provenance.get("report_urls", []) if clean(url)
            }),
            "source_urls": source_urls,
            "retrieved_at_utc_values": provenance.get("retrieved_at_utc_values", []),
        },
    }


def adapt_record(record: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    auction_id = clean(record.get("auction_id"))
    if not auction_id:
        raise ValueError("Candidate is missing auction_id")
    links = provenance_for(auction_id, manifest)
    area = area_metadata(record.get("property_area", {}))
    deadlines = record.get("deadlines")
    if not isinstance(deadlines, dict):
        raise ValueError(f"Missing deadlines for auction {auction_id}")
    flat = {
        "auction_id": auction_id,
        "title": clean(record.get("title")),
        "scheme_name": clean(record.get("scheme_name")),
        "property_number": clean(record.get("property_number")),
        "property_area": area["original"],
        "usage_type": clean(record.get("usage_type")),
        "assessed_value": clean(record.get("assessed_value", {}).get("original")),
        "emd_amount": clean(record.get("emd_amount_original")),
        "emd_start": clean(deadlines.get("emd_start", {}).get("original")),
        "emd_end": clean(deadlines.get("emd_end", {}).get("original")),
        "bid_start": clean(deadlines.get("bid_start", {}).get("original")),
        "bid_end": clean(deadlines.get("bid_end", {}).get("original")),
        "source_authority": clean(record.get("authority")),
        "source_url": links["listing_links"][0],
        "listing_url": links["listing_links"][0],
        "listing_links": links["listing_links"],
        "detail_links": links["detail_links"],
        "source_provenance": links["source_provenance"],
        "retrieved_at_utc": clean(record.get("canonical_selection", {}).get(
            "retrieved_at_utc"
        )),
        "current_status": clean(record.get("status")),
        "raw_row_text": record.get("source_values", {}).get("raw_row_text"),
        "raw_assessed_value_row_text": record.get("source_values", {}).get(
            "raw_assessed_value_row_text"
        ),
        "_jda_staging": {
            "original_values": {
                "property_area": record.get("property_area"),
                "assessed_value": record.get("assessed_value"),
                "deadlines": deadlines,
                "usage_type": record.get("usage_type"),
                "emd_amount": record.get("emd_amount_original"),
            },
            "normalized_area": area,
            "provenance_ref": record.get("provenance_ref"),
            "production_match": record.get("production_match", False),
        },
    }
    missing = [field for field in FLAT_REQUIRED_FIELDS if not clean(flat.get(field))]
    if missing:
        raise ValueError(f"Auction {auction_id} missing flat fields: {missing}")
    return flat


def validate_flat_record(record: dict[str, Any]) -> None:
    missing = [field for field in FLAT_REQUIRED_FIELDS if not clean(record.get(field))]
    if missing:
        raise ValueError(f"Flat record missing fields: {missing}")
    for field in ("listing_url", "source_url"):
        if not str(record[field]).startswith("https://udhonline.rajasthan.gov.in/"):
            raise ValueError(f"Unapproved {field}: {record[field]}")
    for field in ("emd_end", "bid_end"):
        if not re.fullmatch(
            r"\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2} [AP]M", str(record[field])
        ):
            raise ValueError(f"Invalid {field}: {record[field]}")


def build_preview(
    *,
    candidates: dict[str, Any],
    restricted: dict[str, Any],
    manifest: dict[str, Any],
    production: dict[str, Any] | list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if restricted.get("record_count") != 20:
        raise ValueError("Restricted dataset count is not 20")
    adapted = [
        adapt_record(record, manifest)
        for record in candidates.get("records", [])
    ]
    restricted_ids = {
        clean(record.get("auction_id"))
        for record in restricted.get("records", [])
    }
    candidate_ids = {record["auction_id"] for record in adapted}
    if restricted_ids & candidate_ids:
        raise ValueError("Restricted auction ID leaked into candidate dataset")

    production_records = (
        production.get("records", production.get("observations", []))
        if isinstance(production, dict)
        else production
    )
    if not isinstance(production_records, list):
        raise ValueError("Production catalogue records are not a list")
    preserved = [
        record for record in production_records
        if isinstance(record, dict)
    ]
    merged = preserved + adapted
    all_ids = [clean(record.get("auction_id")) for record in merged]
    duplicate_ids = sorted({
        auction_id for auction_id in all_ids
        if auction_id and all_ids.count(auction_id) > 1
    })
    for record in adapted:
        validate_flat_record(record)
    return adapted, merged, {
        "production_observation_count": len(preserved),
        "candidate_record_count": len(adapted),
        "merged_observation_count": len(merged),
        "merged_unique_auction_ids": len(set(all_ids)),
        "duplicate_auction_ids": duplicate_ids,
        "restricted_ids_excluded": len(restricted_ids),
        "restricted_ids_in_merged": len(restricted_ids & set(all_ids)),
    }


def create_outputs(
    *,
    candidates_path: Path = CANDIDATES_FILE,
    restricted_path: Path = RESTRICTED_FILE,
    manifest_path: Path = MANIFEST_FILE,
    production_path: Path = PRODUCTION_FILE,
    output_dir: Path = STAGING_DIR,
    stamp: str = STAMP,
) -> dict[str, Path]:
    candidates = load_object(candidates_path)
    restricted = load_object(restricted_path)
    manifest = load_object(manifest_path)
    production = load_object(production_path)
    adapted, merged, counts = build_preview(
        candidates=candidates,
        restricted=restricted,
        manifest=manifest,
        production=production,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "adapted": output_dir / f"jda_production_adapter_{stamp}.json",
        "merged": output_dir / f"jda_merged_catalogue_preview_{stamp}.json",
        "manifest": output_dir / f"jda_integration_manifest_{stamp}.json",
    }
    if any(path.exists() for path in outputs.values()):
        raise FileExistsError("Refusing to overwrite an existing preview file")
    adapted_payload = {
        "dataset_type": "jda_app_flat_preview",
        "generated_at_utc": datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat(),
        "production_promotion_performed": False,
        "record_count": len(adapted),
        "records": adapted,
    }
    merged_payload = {
        "dataset_type": "jda_merged_catalogue_preview",
        "generated_at_utc": adapted_payload["generated_at_utc"],
        "production_promotion_performed": False,
        "preserved_production_observations": counts["production_observation_count"],
        "added_candidate_records": counts["candidate_record_count"],
        "records": merged,
    }
    integration_manifest = {
        "dataset_type": "jda_app_integration_preview",
        "generated_at_utc": adapted_payload["generated_at_utc"],
        "production_promotion_performed": False,
        "input_files": {
            "candidates": str(candidates_path.relative_to(ROOT)),
            "restricted": str(restricted_path.relative_to(ROOT)),
            "source_manifest": str(manifest_path.relative_to(ROOT)),
            "production": str(production_path.relative_to(ROOT)),
        },
        "output_files": {
            key: str(path.relative_to(ROOT)) for key, path in outputs.items()
        },
        "counts": counts,
        "area_conversion": {
            "square_feet_to_square_metres": SQUARE_FEET_TO_SQUARE_METRES,
            "original_units_preserved": True,
            "silent_unit_interpretation": False,
        },
        "restricted_records_publicly_excluded": True,
    }
    outputs["adapted"].write_text(
        json.dumps(adapted_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    outputs["merged"].write_text(
        json.dumps(merged_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    outputs["manifest"].write_text(
        json.dumps(integration_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stamp", default=STAMP)
    return parser.parse_args()


if __name__ == "__main__":
    result = create_outputs(stamp=parse_args().stamp)
    print(json.dumps({key: str(path) for key, path in result.items()}, indent=2))
