#!/usr/bin/env python3
"""Build an evidence-based geographic matching queue for location batch 1."""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REVIEW = ROOT / "data" / "location_research" / "location_batch1_review.csv"
MANIFEST = ROOT / "data" / "location_research" / "downloads_v2" / "document_manifest.csv"
OCR = ROOT / "data" / "location_research" / "ocr_v1" / "location_evidence_candidates.json"
OUT = ROOT / "data" / "location_research" / "location_batch1_matching_report"

MATCHING = {
    "AAKRITI ENCLAVE - A": {
        "clues": "Manpur Tilawala village, Sanganer; Diggi-Malpura Road; nearby hospital and Silver City; khasra layout.",
        "search_terms": [
            '"Manpur Tilawala" Jaipur',
            '"Diggi Malpura Road" "Manpur Tilawala"',
            '"Silver City" "Manpur Tilawala" Jaipur',
        ],
        "candidate": None,
        "decision": "research_queue",
        "reason": "No unambiguous public place match linked to the plan's road and landmark combination.",
    },
    "ADINATH SAROVAR": {
        "clues": "Manpur Gate village, Phagi tehsil; sector/master-plan roads; khasra plan.",
        "search_terms": [
            '"Manpur Gate" Phagi Jaipur',
            '"Adinath Sarovar" "Manpur Gate"',
            '"Manpur Gate" Phagi JDA GIS',
        ],
        "candidate": {
            "label": "Manpur Gate village",
            "latitude": 26.6680354,
            "longitude": 75.7096410,
            "source_url": "https://nominatim.openstreetmap.org/search?q=Manpur+Gate%2C+Phagi%2C+Jaipur%2C+Rajasthan&format=jsonv2&limit=5",
            "provenance": "OpenStreetMap Nominatim place node 8059854800; display name identifies Manpur Gate, Phagi Tehsil, Jaipur.",
            "confidence": "medium",
            "precision": "village-level",
        },
        "decision": "candidate_not_imported",
        "reason": "The plan confirms the village but not the scheme's position within the village; village centre is not a scheme-area pin.",
    },
    "ADINATH SAROVAR - A": {
        "clues": "Manpur Gate village, Phagi tehsil; Zone 14; ZDP and sector road; commercial plot plan.",
        "search_terms": [
            '"Manpur Gate" Phagi Jaipur',
            '"Adinath Sarovar A" Jaipur',
            '"Manpur Gate" "Zone 14" JDA',
        ],
        "candidate": {
            "label": "Manpur Gate village",
            "latitude": 26.6680354,
            "longitude": 75.7096410,
            "source_url": "https://nominatim.openstreetmap.org/search?q=Manpur+Gate%2C+Phagi%2C+Jaipur%2C+Rajasthan&format=jsonv2&limit=5",
            "provenance": "OpenStreetMap Nominatim place node 8059854800; display name identifies Manpur Gate, Phagi Tehsil, Jaipur.",
            "confidence": "medium",
            "precision": "village-level",
        },
        "decision": "candidate_not_imported",
        "reason": "The plan confirms the village but not the scheme's position within the village; village centre is not a scheme-area pin.",
    },
    "ANANTPURA GROUP HOUSING YOJANA": {
        "clues": "Anantpura village, Sikar Road, Jaipur; NH-11; Anantpura school and nearby roads/landmarks.",
        "search_terms": [
            '"Anantpura" "Sikar Road" Jaipur',
            '"Anantpura school" Sikar Road Jaipur',
            '"Anantpura Group Housing Yojana" Jaipur JDA',
        ],
        "candidate": None,
        "decision": "research_queue",
        "reason": "Public geocoder returns multiple Anantpura villages in Jaipur district; no result is independently tied to Sikar Road and the plan landmarks.",
    },
    "ANUPAM VIHAR - A": {
        "clues": "Palsana/Paldi Parsa area near Ajmer Road; layout roads and khasra boundaries; schematic key plan.",
        "search_terms": [
            '"Anupam Vihar" Palsana Jaipur',
            '"Paldi Parsa" "Ajmer Road" Jaipur',
            '"Anupam Vihar A" JDA Jaipur location plan',
        ],
        "candidate": None,
        "decision": "research_queue",
        "reason": "No authoritative or reliable public coordinate match was found for the combined village/road/scheme clues.",
    },
    "ATAL VIHAR": {
        "clues": "JDA Atal Vihar plan; ZDP/master-plan roads and schematic key map; no unique village anchor.",
        "search_terms": [
            '"Atal Vihar" JDA Jaipur location',
            '"Atal Vihar" Jaipur ZDP',
            '"Atal Vihar" Jaipur GIS',
        ],
        "candidate": None,
        "decision": "research_queue",
        "reason": "The inspected plan does not provide a unique village or landmark that can be cross-checked independently.",
    },
    "BAGRU GREEN": {
        "clues": "Bagru Rewan, Sanganer, Jaipur; master-plan roads; JDA key plan.",
        "search_terms": [
            '"Bagru Rewan" Jaipur',
            '"Bagru Rewan" Sanganer JDA',
            '"Bagru Green" Jaipur location plan',
        ],
        "candidate": None,
        "decision": "research_queue",
        "reason": "Exact Bagru Rewan spelling is not returned by the public geocoder; Bagru alone is too broad to identify the scheme area.",
    },
    "CHETAN VIHAR": {
        "clues": "Mad Rampura village, Sanganer; existing BT road; Narayan Infinity Group Housing; khasra layout.",
        "search_terms": [
            '"Mad Rampura" Sanganer Jaipur',
            '"Narayan Infinity Group Housing" Jaipur',
            '"Chetan Vihar" "Mad Rampura"',
        ],
        "candidate": None,
        "decision": "research_queue",
        "reason": "No independent public result links Mad Rampura, the BT road, and Narayan Infinity to a unique coordinate.",
    },
    "DEENDAYAL NAGAR": {
        "clues": "Bhambori village, Jaipur; master-plan roads, khasra boundaries, and schematic key map.",
        "search_terms": [
            '"Bhambori" Jaipur Rajasthan',
            '"Deendayal Nagar" Bhambori Jaipur',
            '"Bhambori" JDA GIS Jaipur',
        ],
        "candidate": None,
        "decision": "research_queue",
        "reason": "No reliable public place match was found for Bhambori that can be tied to the scheme layout.",
    },
    "DIAMOND CITY": {
        "clues": "Chaturbhujpura urf Bhawanipura, Chaksu; Goner Road; JDA scheme/part map and surrounding roads.",
        "search_terms": [
            '"Chaturbhujpura" Bhawanipura Chaksu',
            '"Diamond City" "Goner Road" Jaipur',
            '"Chaturbhujpura" JDA Jaipur',
        ],
        "candidate": None,
        "decision": "research_queue",
        "reason": "No independent public result identifies the combined revenue-village name and Goner Road relationship.",
    },
}


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    review_rows = {norm(row["scheme_name"]): row for row in read_rows(REVIEW)}
    manifest_by_scheme: dict[str, set[str]] = defaultdict(set)
    for row in read_rows(MANIFEST):
        if row.get("scheme_name") and row.get("document_url"):
            manifest_by_scheme[norm(row["scheme_name"])].add(row["document_url"])

    ocr_data = json.loads(OCR.read_text(encoding="utf-8"))
    ocr_by_scheme: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in ocr_data.get("candidates", []):
        ocr_by_scheme[norm(item.get("scheme_name_from_manifest"))].append(item)

    records = []
    for scheme, matching in MATCHING.items():
        review = review_rows.get(norm(scheme))
        if review is None:
            raise ValueError(f"Batch review has no row for {scheme!r}")
        ocr_items = ocr_by_scheme.get(norm(scheme), [])
        records.append(
            {
                "scheme_name": scheme,
                "normalized_label": norm(scheme),
                "source_plan_urls": sorted(manifest_by_scheme[norm(scheme)]),
                "inspected_plan_files": json.loads(review["inspected_files"]),
                "geographic_clues": matching["clues"],
                "ocr_context_hits": sorted(
                    {
                        hit
                        for item in ocr_items
                        for hit in item.get("place_or_map_text_hits", [])
                    }
                ),
                "exact_search_terms": matching["search_terms"],
                "candidate_match": matching["candidate"],
                "decision": matching["decision"],
                "confidence": (
                    matching["candidate"]["confidence"]
                    if matching["candidate"]
                    else "not established"
                ),
                "precision": (
                    matching["candidate"]["precision"]
                    if matching["candidate"]
                    else "none"
                ),
                "decision_reason": matching["reason"],
                "coordinates_imported": False,
            }
        )

    payload = {
        "generated_from": [
            str(REVIEW.relative_to(ROOT)),
            str(MANIFEST.relative_to(ROOT)),
            str(OCR.relative_to(ROOT)),
        ],
        "method": (
            "Exact geographic clue matching against independent public sources. "
            "Village-level matches are candidates only and are not treated as "
            "scheme-area or exact-plot coordinates."
        ),
        "resolved_count": 0,
        "candidate_not_imported_count": sum(
            record["decision"] == "candidate_not_imported" for record in records
        ),
        "research_queue_count": sum(
            record["decision"] == "research_queue" for record in records
        ),
        "records": records,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    fields = [
        "scheme_name",
        "normalized_label",
        "source_plan_urls",
        "inspected_plan_files",
        "geographic_clues",
        "ocr_context_hits",
        "exact_search_terms",
        "candidate_match",
        "decision",
        "confidence",
        "precision",
        "decision_reason",
        "coordinates_imported",
    ]
    with OUT.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    field: json.dumps(record[field], ensure_ascii=False)
                    if isinstance(record[field], (list, dict))
                    else record[field]
                    for field in fields
                }
            )
    print("Schemes reviewed:", len(records))
    print("Candidate matches not imported:", payload["candidate_not_imported_count"])
    print("Research queue:", payload["research_queue_count"])
    print("Coordinates imported: 0")
    print(OUT.with_suffix(".json"))
    print(OUT.with_suffix(".csv"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
