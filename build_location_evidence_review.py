#!/usr/bin/env python3
"""Build a conservative scheme-location evidence review from local artifacts.

This workflow never geocodes names, infers coordinates from OCR, or updates the
auction catalogue. It records official UDH/JDA document references and
location-context evidence so coordinates can be reviewed and imported later.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "jaipur_land_intelligence.sqlite3"
DEFAULT_OCR = ROOT / "data" / "location_research" / "ocr_v1" / "location_evidence_candidates.json"
DEFAULT_MANIFEST = ROOT / "data" / "location_research" / "downloads_v2" / "document_manifest.csv"
DEFAULT_OUT = ROOT / "data" / "location_research"

CONTEXT_TERMS = {
    "village": re.compile(r"\bvillage\b", re.IGNORECASE),
    "road": re.compile(r"\broad\b", re.IGNORECASE),
    "zone": re.compile(r"\bzone\b", re.IGNORECASE),
    "khasra": re.compile(r"\bkhasra\b", re.IGNORECASE),
    "landmark": re.compile(
        r"\b(near|opposite|behind|adjacent|landmark|hospital|underpass|"
        r"railway|highway|chowk|nagar|gate)\b",
        re.IGNORECASE,
    ),
}


def clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def exact_normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", clean(value)).casefold()


def official_url(value: Any) -> bool:
    try:
        return (urlparse(clean(value)).hostname or "").lower() in {
            "udhonline.rajasthan.gov.in",
            "jda.rajasthan.gov.in",
            "jdafa.rajasthan.gov.in",
        }
    except ValueError:
        return False


def as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [clean(item) for item in value if clean(item)]
    return [clean(value)] if clean(value) else []


def load_ocr(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[str, dict[str, Any]] = {}
    for candidate in payload.get("candidates", []):
        scheme = clean(candidate.get("scheme_name_from_manifest"))
        if not scheme:
            continue
        record = grouped.setdefault(
            exact_normalized(scheme),
            {
                "scheme_name": scheme,
                "document_count": 0,
                "file_paths": set(),
                "context_terms": set(),
                "context_hits": [],
                "coordinate_like_strings": set(),
            },
        )
        record["document_count"] += 1
        file_path = clean(candidate.get("file_path"))
        if file_path:
            record["file_paths"].add(file_path)
        for hit in as_list(candidate.get("place_or_map_text_hits")):
            record["context_hits"].append(hit)
        for value in as_list(candidate.get("coordinate_like_strings")):
            record["coordinate_like_strings"].add(value)
        text = clean(candidate.get("ocr_text"))
        for term, pattern in CONTEXT_TERMS.items():
            if pattern.search(text):
                record["context_terms"].add(term)
    return grouped


def load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            scheme = clean(row.get("scheme_name"))
            if not scheme:
                continue
            record = grouped.setdefault(
                exact_normalized(scheme),
                {
                    "official_urls": set(),
                    "auction_ids": set(),
                    "document_count": 0,
                },
            )
            record["document_count"] += 1
            auction_id = clean(row.get("auction_id"))
            if auction_id:
                record["auction_ids"].add(auction_id)
            for field in ("detail_url", "document_url"):
                url = clean(row.get(field))
                if url and official_url(url):
                    record["official_urls"].add(url)
    return grouped


def build_review(db_path: Path, ocr_path: Path, manifest_path: Path) -> list[dict[str, Any]]:
    ocr = load_ocr(ocr_path)
    manifest = load_manifest(manifest_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        labels = connection.execute(
            """
            SELECT sl.id, sl.label, sl.normalized_label,
                   l.latitude, l.longitude, l.precision, l.status,
                   l.source_url, l.evidence_notes
            FROM scheme_labels AS sl
            LEFT JOIN locations AS l ON l.scheme_label_id = sl.id
            ORDER BY sl.label
            """
        ).fetchall()

    review: list[dict[str, Any]] = []
    for label in labels:
        key = exact_normalized(label["label"])
        ocr_record = ocr.get(key, {})
        manifest_record = manifest.get(key, {})
        context_terms = sorted(ocr_record.get("context_terms", set()))
        context_hits = list(dict.fromkeys(ocr_record.get("context_hits", [])))
        coordinate_like = sorted(ocr_record.get("coordinate_like_strings", set()))
        has_location = label["latitude"] is not None and label["longitude"] is not None
        review.append(
            {
                "scheme_label_id": label["id"],
                "scheme_name": label["label"],
                "normalized_label": label["normalized_label"],
                "auction_ids": sorted(manifest_record.get("auction_ids", set())),
                "official_reference_count": len(manifest_record.get("official_urls", set())),
                "official_reference_urls": sorted(manifest_record.get("official_urls", set())),
                "ocr_document_count": ocr_record.get("document_count", 0),
                "ocr_files": sorted(ocr_record.get("file_paths", set())),
                "context_terms": context_terms,
                "context_hits": context_hits,
                "coordinate_like_strings": coordinate_like,
                "coordinate_candidate_status": (
                    "resolved_existing_location" if has_location
                    else "unresolved_no_coordinate_evidence"
                ),
                "latitude": label["latitude"],
                "longitude": label["longitude"],
                "precision": label["precision"],
                "status": label["status"],
                "source_url": label["source_url"],
                "evidence_notes": label["evidence_notes"],
                "review_note": (
                    "Existing scheme-area location retained; not an exact plot location."
                    if has_location
                    else (
                        "Context evidence found, but no valid coordinate source exists "
                        "in the repository. Do not geocode or infer a village centre."
                        if context_terms or manifest_record.get("official_urls")
                        else "No OCR or official document evidence matched this label."
                    )
                ),
            }
        )
    return review


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--ocr", type=Path, default=DEFAULT_OCR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    review = build_review(args.db, args.ocr, args.manifest)
    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / "location_evidence_review.json"
    csv_path = args.out / "location_evidence_review.csv"
    json_path.write_text(
        json.dumps(
            {
                "coordinate_policy": (
                    "Only existing database locations are reported as resolved. "
                    "OCR context and official document references do not create coordinates."
                ),
                "scheme_count": len(review),
                "resolved_count": sum(
                    row["coordinate_candidate_status"] == "resolved_existing_location"
                    for row in review
                ),
                "unresolved_count": sum(
                    row["coordinate_candidate_status"] != "resolved_existing_location"
                    for row in review
                ),
                "records": review,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fields = list(review[0]) if review else []
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in review:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict))
                    else value
                    for key, value in row.items()
                }
            )

    resolved = sum(row["coordinate_candidate_status"] == "resolved_existing_location" for row in review)
    print(f"Scheme labels reviewed: {len(review)}")
    print(f"Existing scheme-area locations: {resolved}")
    print(f"Additional coordinates imported: 0")
    print(f"Unresolved labels: {len(review) - resolved}")
    print(f"JSON review: {json_path}")
    print(f"CSV review: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
