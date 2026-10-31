import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import refresh_pipeline
import promote_staging


class RefreshPipelineTests(unittest.TestCase):
    def test_riico_mixed_zone_results_are_partial_and_cached_failures_are_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            old_cache = refresh_pipeline.RIICO_CACHE_DIR
            refresh_pipeline.RIICO_CACHE_DIR = Path(directory)
            try:
                record = {
                    "record_key": "riico-eauction:1",
                    "original_identifier": "1",
                    "last_checked_at": "2026-10-31T00:00:00+05:30",
                    "notes": "official",
                }
                refresh_pipeline._write_riico_cache("auction", "15", [record], "2026-10-31T00:00:00+05:30")
                def collect(unit_id, *args, **kwargs):
                    if unit_id == "18":
                        return [dict(record, record_key="riico-eauction:2")]
                    raise requests.ConnectTimeout("zone unavailable")
                with patch("refresh_pipeline.riico.collect_unit", side_effect=collect), \
                     patch("refresh_pipeline.time.sleep"):
                    records, result = refresh_pipeline.collect_riico(
                        Mock(), refresh_pipeline.datetime.now(refresh_pipeline.IST),
                        0, retries=1, connect_timeout=1, read_timeout=2,
                    )
            finally:
                refresh_pipeline.RIICO_CACHE_DIR = old_cache
            self.assertEqual(result["fetch_status"], "partial_success")
            self.assertEqual(result["successful_zones"], 1)
            self.assertEqual(result["stale_zones"], 1)
            self.assertGreaterEqual(result["failed_zones"], 1)
            self.assertTrue(any(row.get("stale_data") for row in records))

    def test_riico_all_zone_failure_has_no_successful_records(self):
        with tempfile.TemporaryDirectory() as directory:
            old_cache = refresh_pipeline.RIICO_CACHE_DIR
            refresh_pipeline.RIICO_CACHE_DIR = Path(directory)
            try:
                with patch("refresh_pipeline.riico.collect_unit", side_effect=requests.ConnectTimeout("down")), \
                     patch("refresh_pipeline.time.sleep"):
                    records, result = refresh_pipeline.collect_riico(
                        Mock(), refresh_pipeline.datetime.now(refresh_pipeline.IST),
                        0, retries=1, connect_timeout=1, read_timeout=2,
                    )
            finally:
                refresh_pipeline.RIICO_CACHE_DIR = old_cache
            self.assertEqual(records, [])
            self.assertEqual(result["fetch_status"], "failure")
            self.assertEqual(result["failed_zones"], len(refresh_pipeline.riico.UNITS) + 1)
            self.assertEqual(result["error_summaries"][0].split(": ", 1)[0], "ConnectTimeout")

    def test_riico_retry_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            old_cache = refresh_pipeline.RIICO_CACHE_DIR
            refresh_pipeline.RIICO_CACHE_DIR = Path(directory)
            try:
                with patch("refresh_pipeline.riico.collect_unit", side_effect=requests.Timeout("slow")) as collect, \
                     patch("refresh_pipeline.time.sleep"):
                    refresh_pipeline.collect_riico(
                        Mock(), refresh_pipeline.datetime.now(refresh_pipeline.IST),
                        0, retries=2, connect_timeout=1, read_timeout=2,
                    )
            finally:
                refresh_pipeline.RIICO_CACHE_DIR = old_cache
            self.assertEqual(collect.call_count, (2 + 1) * (len(refresh_pipeline.riico.UNITS) + 1))

    def test_riico_direct_cache_fallback_is_partial_and_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            old_cache = refresh_pipeline.RIICO_CACHE_DIR
            refresh_pipeline.RIICO_CACHE_DIR = Path(directory)
            try:
                refresh_pipeline._write_riico_cache(
                    "direct", "18", {"unit": "cached", "result": "no_records"},
                    "2026-10-31T00:00:00+05:30",
                )
                def check(unit_id, *args, **kwargs):
                    if unit_id == "18":
                        return {"unit": "fresh", "result": "records_present_review_required"}
                    raise requests.Timeout("slow")
                with patch("refresh_pipeline.riico.check_direct", side_effect=check), \
                     patch("refresh_pipeline.time.sleep"):
                    result = refresh_pipeline.collect_direct(
                        Mock(), 0, retries=0, connect_timeout=1, read_timeout=2,
                    )
            finally:
                refresh_pipeline.RIICO_CACHE_DIR = old_cache
            self.assertEqual(result["fetch_status"], "partial_success")
            self.assertEqual(result["stale_zones"], 1)
            self.assertTrue(any(row.get("stale_data") for row in result["checks"]))

    def _udh_record(self, auction_id="1"):
        return {
            "auction_id": auction_id,
            "title": "Plot",
            "scheme_name": "Scheme",
            "property_number": "A-1",
            "property_area": "100 Sqm",
            "emd_end": "30-Oct-2026 05:00 PM",
            "bid_end": "31-Oct-2026 05:00 PM",
            "source_url": "https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=x",
        }

    def test_validate_catalogue_rejects_empty_and_malformed_dates(self):
        with self.assertRaises(ValueError):
            refresh_pipeline.validate_catalogue_records([])
        malformed = self._udh_record()
        malformed["bid_end"] = "not-a-date"
        with self.assertRaises(ValueError):
            refresh_pipeline.validate_catalogue_records([malformed])

    def test_collect_udh_catalogue_fetches_all_discovered_schemes_and_preserves_duplicates(self):
        entries = [
            {"scheme_name": "One", "official_listing_url": "https://udhonline.rajasthan.gov.in/one", "reported_auction_count": 1},
            {"scheme_name": "Two", "official_listing_url": "https://udhonline.rajasthan.gov.in/two", "reported_auction_count": 1},
        ]
        records = [self._udh_record("1"), self._udh_record("1")]
        with patch("refresh_pipeline.discover_udh_entries", return_value=entries), \
             patch("refresh_pipeline.fetch", side_effect=[("<one>", 200, False), ("<two>", 200, False)]), \
             patch("refresh_pipeline.parse_page", side_effect=[
                 ([records[0]], "one"), ([records[1]], "two")
             ]):
            result, details = refresh_pipeline.collect_udh_catalogue(
                Mock(), retries=1, interval=0, cache_ttl=0, use_cache=False,
            )
        self.assertEqual(len(result), 2)
        self.assertEqual(details["scheme_count"], 2)

    def test_catalogue_promotion_is_atomic_and_rejects_invalid_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "auctions.json"
            target.write_text('{"records": [{"auction_id": "old"}]}', encoding="utf-8")
            old_path = refresh_pipeline.CATALOGUE_FILE
            refresh_pipeline.CATALOGUE_FILE = target
            try:
                with self.assertRaises(ValueError):
                    refresh_pipeline.promote_catalogue_records([self._udh_record("new") | {"bid_end": "bad"}])
                self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["records"][0]["auction_id"], "old")
            finally:
                refresh_pipeline.CATALOGUE_FILE = old_path

    def test_catalogue_promotion_preserves_prior_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "auctions.json"
            old = self._udh_record("old")
            target.write_text(json.dumps({"records": [old]}), encoding="utf-8")
            old_path = refresh_pipeline.CATALOGUE_FILE
            refresh_pipeline.CATALOGUE_FILE = target
            try:
                count = refresh_pipeline.promote_catalogue_records([self._udh_record("new")])
                payload = json.loads(target.read_text(encoding="utf-8"))
            finally:
                refresh_pipeline.CATALOGUE_FILE = old_path
            self.assertEqual(count, 2)
            self.assertEqual({row["auction_id"] for row in payload["records"]}, {"old", "new"})

    def test_promote_post_auction_records_merges_without_deleting_history(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "jda_post_auction_latest.json"
            target.write_text(json.dumps([{
                "record_fingerprint": "old",
                "auction_date": "01/01/2025",
            }]), encoding="utf-8")
            old_path = refresh_pipeline.POST_AUCTION_FILE
            refresh_pipeline.POST_AUCTION_FILE = target
            try:
                count = refresh_pipeline.promote_post_auction_records([
                    {"record_fingerprint": "old", "auction_date": "01/01/2025"},
                    {"record_fingerprint": "new", "auction_date": "02/01/2025"},
                ])
                payload = json.loads(target.read_text(encoding="utf-8"))
            finally:
                refresh_pipeline.POST_AUCTION_FILE = old_path
            self.assertEqual(count, 2)
            self.assertEqual(
                {row["record_fingerprint"] for row in payload},
                {"old", "new"},
            )

    def test_promote_post_auction_rejects_invalid_existing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "jda_post_auction_latest.json"
            target.write_text(json.dumps({"records": []}), encoding="utf-8")
            old_path = refresh_pipeline.POST_AUCTION_FILE
            refresh_pipeline.POST_AUCTION_FILE = target
            try:
                with self.assertRaises(ValueError):
                    refresh_pipeline.promote_post_auction_records([
                        {"record_fingerprint": "new"},
                    ])
            finally:
                refresh_pipeline.POST_AUCTION_FILE = old_path

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
