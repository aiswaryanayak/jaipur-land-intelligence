import json
import tempfile
import unittest
from pathlib import Path

import prepare_jda_production_preview as adapter


class PrepareJdaProductionPreviewTests(unittest.TestCase):
    def test_area_conversion_preserves_square_feet_and_converts_to_square_metres(self):
        metadata = adapter.area_metadata({
            "original": "100 Square Feet",
            "normalized": {"value": 100, "unit": "Square Feet"},
        })
        self.assertEqual(metadata["original"], "100 Square Feet")
        self.assertEqual(metadata["unit"], "Square Feet")
        self.assertAlmostEqual(metadata["square_metre_value"], 9.290304)

    def test_adapt_record_flattens_fields_and_expands_provenance(self):
        record = {
            "auction_id": "1",
            "authority": "JDA",
            "title": "Plot",
            "scheme_name": "Scheme",
            "property_number": "A-1",
            "property_area": {
                "original": "10 Square Meter",
                "normalized": {"value": 10, "unit": "Square Meter"},
            },
            "assessed_value": {
                "original": "1,000.00 (INR)",
                "normalized": {"inr": 1000},
            },
            "usage_type": "Residential",
            "emd_amount_original": "100",
            "deadlines": {
                key: {"original": value}
                for key, value in {
                    "emd_start": "10-Nov-2026 10:00 AM",
                    "emd_end": "11-Nov-2026 10:00 AM",
                    "bid_start": "10-Nov-2026 10:00 AM",
                    "bid_end": "12-Nov-2026 10:00 AM",
                }.items()
            },
            "canonical_selection": {
                "retrieved_at_utc": "2026-11-10T00:00:00+00:00"
            },
            "status": "upcoming",
            "source_values": {},
        }
        manifest = {"provenance": {"1": {
            "listing_urls": ["https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=1"],
            "source_urls": ["https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=1"],
            "report_urls": ["https://udhonline.rajasthan.gov.in/Portal/LiveAuctionDetailReport?q=1"],
            "detail_urls": [],
        }}}
        adapted = adapter.adapt_record(record, manifest)
        self.assertEqual(adapted["property_area"], "10 Square Meter")
        self.assertEqual(adapted["emd_end"], "11-Nov-2026 10:00 AM")
        self.assertEqual(len(adapted["listing_links"]), 1)
        self.assertEqual(len(adapted["detail_links"]), 1)
        self.assertEqual(adapted["_jda_staging"]["normalized_area"]["square_metre_value"], 10)

    def test_merge_preserves_production_observations_and_excludes_restricted_ids(self):
        candidate = {"records": []}
        restricted = {"record_count": 20, "records": []}
        manifest = {"provenance": {}}
        production = {"records": [{"auction_id": "old", "title": "Existing"}]}
        adapted, merged, counts = adapter.build_preview(
            candidates=candidate,
            restricted=restricted,
            manifest=manifest,
            production=production,
        )
        self.assertEqual(adapted, [])
        self.assertEqual(merged, production["records"])
        self.assertEqual(counts["production_observation_count"], 1)
        self.assertEqual(counts["restricted_ids_in_merged"], 0)


if __name__ == "__main__":
    unittest.main()
