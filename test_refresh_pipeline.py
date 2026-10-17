import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import refresh_pipeline
import promote_staging


class RefreshPipelineTests(unittest.TestCase):
    def test_run_id_is_utc_like_and_stable(self):
        value = refresh_pipeline.run_id(refresh_pipeline.datetime(2026, 10, 18, 0, 0, tzinfo=refresh_pipeline.UTC))
        self.assertEqual(value, "20261018T000000Z")

    def test_cache_key_is_stable(self):
        self.assertEqual(
            refresh_pipeline.cache_key("https://example.test"),
            refresh_pipeline.cache_key("https://example.test"),
        )

    def test_fetch_uses_fresh_cache_without_request(self):
        with tempfile.TemporaryDirectory() as directory:
            old_cache = refresh_pipeline.CACHE_DIR
            refresh_pipeline.CACHE_DIR = Path(directory)
            try:
                session = Mock()
                response = Mock(status_code=200, text="<html>ok</html>")
                session.get.return_value = response
                first = refresh_pipeline.fetch(
                    session, "https://example.test", retries=0, interval=0,
                    cache_ttl=3600, use_cache=False,
                )
                second = refresh_pipeline.fetch(
                    session, "https://example.test", retries=0, interval=0,
                    cache_ttl=3600, use_cache=True,
                )
                self.assertEqual(first[0], second[0])
                self.assertTrue(second[2])
                self.assertEqual(session.get.call_count, 1)
            finally:
                refresh_pipeline.CACHE_DIR = old_cache

    def test_failed_source_isolated_in_manifest_shape(self):
        result = refresh_pipeline.source_result("source", "https://example.test", refresh_pipeline.utc_now())
        refresh_pipeline.finish(result, "failure", error="HTTP 429")
        self.assertEqual(result["fetch_status"], "failure")
        self.assertEqual(result["record_count"], 0)
        self.assertEqual(result["error"], "HTTP 429")

    def test_retry_after_is_respected_for_rate_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            old_cache = refresh_pipeline.CACHE_DIR
            refresh_pipeline.CACHE_DIR = Path(directory)
            try:
                session = Mock()
                limited = Mock(status_code=429, headers={"Retry-After": "7"})
                success = Mock(status_code=200, text="ok", headers={})
                session.get.side_effect = [limited, success]
                limited.raise_for_status.side_effect = __import__(
                    "requests"
                ).HTTPError("429")
                with patch("refresh_pipeline.time.sleep") as sleep:
                    body, status, cached = refresh_pipeline.fetch(
                        session, "https://example.test/rate", retries=1,
                        interval=0, cache_ttl=0, use_cache=False,
                    )
                self.assertEqual((body, status, cached), ("ok", 200, False))
                self.assertIn(7.0, [call.args[0] for call in sleep.call_args_list])
            finally:
                refresh_pipeline.CACHE_DIR = old_cache

    def test_promotion_dry_run_skips_unapproved_records(self):
        records = [{
            "authority": "RIICO",
            "source_type": "e-auction",
            "source_url": "https://example.test/grid",
            "original_identifier": "1",
            "record_key": "riico-eauction:1",
            "title": "Plot A",
            "property_or_plot_number": "A-1",
            "scheme_or_industrial_area": "Area",
            "land_use_category": "Industrial",
            "area": "10 Sqm",
            "reserve_price_or_rate": "100 Rs. Per Sqm",
            "emd": "10",
            "application_start": "01-Oct-2026 10:00 AM",
            "application_end": "02-Oct-2026 10:00 AM",
            "bid_start": "03-Oct-2026 10:00 AM",
            "bid_end": "04-Oct-2026 10:00 AM",
            "current_status": "closed",
            "last_checked_at": "2026-10-18T00:00:00+00:00",
            "review_status": "manual_review",
        }]
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "test.sqlite3"
            import sqlite3
            connection = sqlite3.connect(db)
            connection.execute("CREATE TABLE auctions (auction_id TEXT PRIMARY KEY, source_authority TEXT, record_json TEXT)")
            connection.close()
            result = promote_staging.preview(records, db)
            self.assertEqual(result["additions"], [])
            self.assertEqual(len(result["skipped"]), 1)

    def test_scheduler_preview_is_non_registering(self):
        setup = Path("setup_refresh_task.ps1").read_text(encoding="utf-8")
        runner = Path("run_refresh_task.ps1").read_text(encoding="utf-8")
        self.assertIn("if (-not $Register)", setup)
        self.assertIn("Register-ScheduledTask", setup)
        self.assertIn("refresh_pipeline.py", runner)
        self.assertIn("LASTEXITCODE", runner)


if __name__ == "__main__":
    unittest.main()
