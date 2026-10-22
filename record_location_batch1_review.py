#!/usr/bin/env python3
"""Record the first ten source-driven location-resolution decisions.

The decisions are based on visual inspection of the downloaded official JDA/UDH
plans, not OCR alone. No coordinates are created by this workflow.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "data" / "location_research" / "downloads_v2" / "document_manifest.csv"
OUT_DIR = ROOT / "data" / "location_research"

BATCH = {
    "AAKRITI ENCLAVE - A": {
        "anchors": "Village Manpur Tilawala, Sanganer; Diggi Malpura Road; sector roads; hospital; Silver City; khasra plan.",
        "inspected_files": [
            "data/location_research/downloads_v2/44794_Location_Plan_Documents_a0c296b283.jpg",
            "data/location_research/downloads_v2/44794_Site_Plan_of_the_Plot_Issued_by_the_Development_Authority_UIT_229d6710eb.jpg",
        ],
    },
    "ADINATH SAROVAR": {
        "anchors": "Village Manpur Gate, Tehsil Phagi; sector-plan roads; khsara plan; no georeferenced control.",
        "inspected_files": [
            "data/location_research/downloads_v2/44877_Location_Plan_Documents_c618570fa1.jpg",
            "data/location_research/downloads_v2/44877_Site_Plan_of_the_Plot_Issued_by_the_Development_Authority_UIT_f973a561f9.jpg",
        ],
    },
    "ADINATH SAROVAR - A": {
        "anchors": "Village Manpur Gate, Tehsil Phagi; ZDP/sector roads; Zone 14; plot plan dimensions.",
        "inspected_files": [
            "data/location_research/downloads_v2/44737_Site_Plan_of_the_Plot_Issued_by_the_Development_Authority_UIT_f60a69638d.jpg",
            "data/location_research/downloads_v2/44877_Location_Plan_Documents_c618570fa1.jpg",
        ],
    },
    "ANANTPURA GROUP HOUSING YOJANA": {
        "anchors": "Village Anantpura, Sikar Road, Jaipur; NH-11; Anantpura school; road junctions and hospitals.",
        "inspected_files": [
            "data/location_research/downloads_v2/44900_Location_Plan_Documents_74de2ae30e.jpg",
            "data/location_research/downloads_v2/44900_Site_Plan_of_the_Plot_Issued_by_the_Development_Authority_UIT_745e542182.jpg",
        ],
    },
    "ANUPAM VIHAR - A": {
        "anchors": "JDA Anupam Vihar layout; roads and khasra boundaries; key plan is schematic and not georeferenced.",
        "inspected_files": [
            "data/location_research/downloads_v2/44838_Location_Plan_Documents_2bec436238.jpg",
            "data/location_research/downloads_v2/44838_Site_Plan_of_the_Plot_Issued_by_the_Development_Authority_UIT_64d0499436.jpg",
        ],
    },
    "ATAL VIHAR": {
        "anchors": "JDA Atal Vihar layout; ZDP road and road network; no village coordinate or georeferenced control.",
        "inspected_files": [
            "data/location_research/downloads_v2/44801_Location_Plan_Documents_b6cd0cd0e7.jpg",
        ],
    },
    "BAGRU GREEN": {
        "anchors": "Village Bagru Rewan, Tehsil Sanganer, District Jaipur; master-plan roads; no coordinate.",
        "inspected_files": [
            "data/location_research/downloads_v2/44881_Location_Plan_Documents_55dbdb9680.jpg",
        ],
    },
    "CHETAN VIHAR": {
        "anchors": "Village Mad rampura, Tehsil Sanganer; existing BT road; Narayan Infinity Group Housing; khasra plan.",
        "inspected_files": [
            "data/location_research/downloads_v2/44892_Location_Plan_Documents_124f9f7d7d.jpg",
            "data/location_research/downloads_v2/44892_Site_Plan_of_the_Plot_Issued_by_the_Development_Authority_UIT_bdade552a1.jpg",
        ],
    },
    "DEENDAYAL NAGAR": {
        "anchors": "Village Bhambori, Tehsil/District Jaipur; master-plan roads; plot plan; key plan lacks coordinates.",
        "inspected_files": [
            "data/location_research/downloads_v2/44748_Location_Plan_Documents_227a4e5ae9.jpg",
            "data/location_research/downloads_v2/44752_Site_Plan_of_the_Plot_Issued_by_the_Development_Authority_UIT_aa8b8df0b4.jpg",
        ],
    },
    "DIAMOND CITY": {
        "anchors": "Village Chaturbhujpura urf Bhawanipura, Tehsil Chaksu; Goner Road; JDA scheme Pratap Nagar and nearby roads.",
        "inspected_files": [
            "data/location_research/downloads_v2/44950_Location_Plan_Documents_ccee60a5a5.jpg",
            "data/location_research/downloads_v2/44950_Site_Plan_of_the_Plot_Issued_by_the_Development_Authority_UIT_a96776b911.jpg",
        ],
    },
}


def normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def load_manifest() -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    with MANIFEST.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            grouped[normalized(row.get("scheme_name"))].append(row)
    return grouped


def main() -> int:
    manifest = load_manifest()
    records = []
    for scheme, decision in BATCH.items():
        rows = manifest.get(normalized(scheme), [])
        source_urls = sorted(
            {
                row["document_url"].strip()
                for row in rows
                if row.get("document_url", "").strip()
            }
        )
        records.append(
            {
                "scheme_name": scheme,
                "status": "rejected_for_coordinate_import",
                "confidence": "high",
                "precision": "none",
                "latitude": None,
                "longitude": None,
                "source_urls": source_urls,
                "official_document_count": len(rows),
                "inspected_files": decision["inspected_files"],
                "anchors": decision["anchors"],
                "evidence_notes": (
                    "Official JDA/UDH layout and plot/site-plan images were inspected. "
                    "They establish local context and plot relationships but provide no "
                    "coordinate pair, georeferenced control, or official GIS geometry. "
                    "A village, road, or landmark is not being converted into a guessed "
                    "scheme-area coordinate."
                ),
                "rejection_reason": (
                    "Insufficient source-to-coordinate match for a defensible "
                    "scheme-area pin; retain as unresolved."
                ),
            }
        )

    payload = {
        "batch": 1,
        "decision_policy": (
            "Import only coordinates supported by a defensible source-to-location "
            "match; these plans did not meet that threshold."
        ),
        "resolved_count": 0,
        "rejected_count": len(records),
        "unresolved_count": len(records),
        "records": records,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "location_batch1_review.json"
    csv_path = OUT_DIR / "location_batch1_review.csv"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    fields = list(records[0])
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict))
                    else value
                    for key, value in record.items()
                }
            )

    print(f"Batch schemes reviewed: {len(records)}")
    print("Resolved: 0")
    print(f"Rejected for coordinate import: {len(records)}")
    print(f"Unresolved: {len(records)}")
    print(json_path)
    print(csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
