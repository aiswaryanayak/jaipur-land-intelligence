import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import refresh_pipeline
import resume_jda_fresh_revalidation as resume


def make_fixture(root: Path) -> tuple[Path, Path, dict]:
    discovery_path = root / "discovery.json"
    checkpoint_path = root / "jda_fresh_revalidation_progress_20261110T000000Z.json"
    urls = [
        f"https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q={name}"
        for name in ("failed", "success", "pending-a", "pending-b")
    ]
    discovery_path.write_text(json.dumps({
        "scheme_entries": [
            {"official_listing_url": url, "discovery_status": "current", "scheme_name": url}
            for url in urls
        ],
    }), encoding="utf-8")
    original = {
        "entries": {
            urls[0]: {"status": "failed", "error_type": "RuntimeError"},
            urls[1]: {
                "status": "success",
                "retrieved_at_utc": "2026-11-10T00:00:00+00:00",
                "records": [{"auction_id": "old"}],
            },
            urls[2]: {"status": "pending"},
            urls[3]: {"status": "pending"},
        },
    }
    checkpoint_path.write_text(json.dumps(original), encoding="utf-8")
    return discovery_path, checkpoint_path, original


class FreshRevalidationResumeTests(unittest.TestCase):
    def test_isolation_rejects_original_checkpoint(self):
        with self.assertRaises(ValueError):
            resume.validate_checkpoint_path(resume.ORIGINAL_CHECKPOINT)

    def test_successful_entry_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            discovery, checkpoint, original = make_fixture(Path(directory))
            with patch.object(
                resume.refresh_pipeline,
                "fetch",
                side_effect=RuntimeError("stop"),
            ):
                resume.resume(
                    checkpoint_path=checkpoint,
                    discovery_path=discovery,
                    limit=20,
                )
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["entries"][
                    next(url for url, value in original["entries"].items()
                         if value["status"] == "success")
                ],
                original["entries"][
                    next(url for url, value in original["entries"].items()
                         if value["status"] == "success")
                ],
            )

    def test_failed_url_is_retried_once_and_stops_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            discovery, checkpoint, _ = make_fixture(Path(directory))
            with patch.object(
                resume.refresh_pipeline,
                "fetch",
                side_effect=RuntimeError("timeout"),
            ) as fetch:
                result = resume.resume(
                    checkpoint_path=checkpoint,
                    discovery_path=discovery,
                    limit=20,
                )
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(result["stopped"], "RuntimeError")
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["entries"][next(iter(saved["entries"]))]["status"],
                "failed",
            )

    def test_batch_limit_is_twenty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            discovery, checkpoint, _ = make_fixture(root)
            payload = json.loads(discovery.read_text(encoding="utf-8"))
            for index in range(30):
                url = f"https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q={index}"
                payload["scheme_entries"].append({
                    "official_listing_url": url,
                    "discovery_status": "current",
                })
                json.loads(checkpoint.read_text(encoding="utf-8"))["entries"][url] = {
                    "status": "pending"
                }
            progress = json.loads(checkpoint.read_text(encoding="utf-8"))
            for index in range(30):
                url = f"https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q={index}"
                progress["entries"][url] = {"status": "pending"}
            discovery.write_text(json.dumps(payload), encoding="utf-8")
            checkpoint.write_text(json.dumps(progress), encoding="utf-8")
            with patch.object(
                resume.refresh_pipeline,
                "fetch",
                return_value=("<html />", 200, False),
            ) as fetch, patch.object(
                resume.refresh_pipeline,
                "parse_page",
                return_value=([{
                    "auction_id": "new",
                    "title": "New",
                    "scheme_name": "Scheme",
                    "property_number": "1",
                    "property_area": "1 Square Meter",
                    "emd_end": "17-Nov-2026 12:00 PM",
                    "bid_end": "18-Nov-2026 01:30 PM",
                    "source_url": "https://udhonline.rajasthan.gov.in/",
                    }], None),
            ) as parse_page, patch.object(
                resume.refresh_pipeline,
                "validate_catalogue_records",
                side_effect=lambda records: records,
            ):
                resume.resume(checkpoint_path=checkpoint, discovery_path=discovery)
            self.assertEqual(fetch.call_count, 20)

    def test_429_is_distinct_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            discovery, checkpoint, _ = make_fixture(Path(directory))
            with patch.object(
                resume.refresh_pipeline,
                "fetch",
                side_effect=refresh_pipeline.RateLimitedError("url", "30"),
            ):
                result = resume.resume(
                    checkpoint_path=checkpoint,
                    discovery_path=discovery,
                )
            self.assertEqual(result["stopped"], "HTTP 429")
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["entries"][next(iter(saved["entries"]))]["status"],
                "rate_limited",
            )


if __name__ == "__main__":
    unittest.main()
