import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from requests.exceptions import ProxyError
import refresh_pipeline
import promote_staging
import render_connectivity
import run_jda_batches


class RefreshPipelineTests(unittest.TestCase):
    def test_jda_batch_selection_skips_terminal_checkpoint_entries(self):
        discovery = {"scheme_entries": [
            {"official_listing_url": "https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=success", "discovery_status": "current"},
            {"official_listing_url": "https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=failed", "discovery_status": "current"},
            {"official_listing_url": "https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=new", "discovery_status": "current"},
        ]}
        progress = {"entries": {
            discovery["scheme_entries"][0]["official_listing_url"]: {"status": "success"},
            discovery["scheme_entries"][1]["official_listing_url"]: {"status": "failed"},
        }}
        selected = run_jda_batches.select_entries(
            discovery, progress, pages=20, remaining=False,
        )
        self.assertEqual(
            [entry["official_listing_url"] for entry in selected],
            [discovery["scheme_entries"][2]["official_listing_url"]],
        )

    def test_jda_dry_run_preserves_checkpoint_and_reports_cumulative_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            discovery = root / "discovery.json"
            progress = root / "progress.json"
            url = "https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=current"
            discovery.write_text(json.dumps({"scheme_entries": [{
                "official_listing_url": url,
                "discovery_status": "current",
            }]}), encoding="utf-8")
            original = {"entries": {url: {
                "status": "success",
                "records": [{"auction_id": "1", "emd_end": "17-Nov-2026 12:00 PM", "bid_end": "18-Nov-2026 01:30 PM"}],
            }}}
            progress.write_text(json.dumps(original), encoding="utf-8")
            result = run_jda_batches.collect(
                pages=20, dry_run=True, discovery_file=discovery,
                progress_file=progress,
            )
            self.assertEqual(result["selected_pages"], 0)
            self.assertEqual(result["unique_records"], 1)
            self.assertEqual(json.loads(progress.read_text(encoding="utf-8")), original)

    def test_jda_batch_429_is_checkpointed_and_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            discovery = root / "discovery.json"
            progress = root / "progress.json"
            url = "https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=current"
            discovery.write_text(json.dumps({"scheme_entries": [{
                "official_listing_url": url,
                "discovery_status": "current",
            }]}), encoding="utf-8")
            with patch(
                "run_jda_batches.refresh_pipeline.fetch",
                side_effect=refresh_pipeline.RateLimitedError(url, "30"),
            ) as fetch:
                result = run_jda_batches.collect(
                    pages=20, discovery_file=discovery, progress_file=progress,
                )
            self.assertEqual(result["stopped"], "HTTP 429")
            self.assertEqual(fetch.call_count, 1)
            saved = json.loads(progress.read_text(encoding="utf-8"))
            self.assertEqual(saved["entries"][url]["status"], "rate_limited")

    def test_discovery_merges_current_urls_and_preserves_historical_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            old_file = refresh_pipeline.DISCOVERY_FILE
            refresh_pipeline.DISCOVERY_FILE = Path(directory) / "discovery.json"
            refresh_pipeline.DISCOVERY_FILE.write_text(json.dumps({
                "scheme_entries": [{
                    "official_listing_url": "https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=old",
                    "scheme_name": "Old scheme",
                    "reported_auction_count": 1,
                }],
            }), encoding="utf-8")
            try:
                index = (
                    '<a href="/Portal/LiveAuctionDetailReport?q=report">report</a>'
                )
                report = (
                    '<tr><td><a href="/Portal/PortalAuctionList?q=new">'
                    'New scheme 2</a></td></tr>'
                )
                with patch("refresh_pipeline.fetch", side_effect=[
                    (index, 200, False), (report, 200, False)
                ]):
                    entries = refresh_pipeline.discover_udh_entries(
                        Mock(), retries=0, interval=0, cache_ttl=0,
                        use_cache=False,
                    )
                payload = json.loads(refresh_pipeline.DISCOVERY_FILE.read_text(encoding="utf-8"))
            finally:
                refresh_pipeline.DISCOVERY_FILE = old_file
            self.assertEqual(len(entries), 1)
            self.assertEqual(payload["current_scheme_entry_count"], 1)
            statuses = {
                item["official_listing_url"]: item["discovery_status"]
                for item in payload["scheme_entries"]
            }
            self.assertEqual(statuses["https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=new"], "current")
            self.assertEqual(statuses["https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=old"], "historical")

    def test_rate_limit_stops_collection_and_writes_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            progress = Path(directory) / "progress.json"
            with patch("refresh_pipeline.discover_udh_entries", return_value=[{
                "official_listing_url": "https://udhonline.rajasthan.gov.in/Portal/PortalAuctionList?q=new",
                "scheme_name": "New",
                "reported_auction_count": 1,
            }]), patch(
                "refresh_pipeline.fetch",
                side_effect=refresh_pipeline.RateLimitedError("https://example", "30"),
            ):
                with self.assertRaises(refresh_pipeline.RateLimitedError):
                    refresh_pipeline.collect_udh_catalogue(
                        Mock(), retries=0, interval=0, cache_ttl=0,
                        use_cache=False, progress_file=progress,
                    )
            checkpoint = json.loads(progress.read_text(encoding="utf-8"))
            result = next(iter(checkpoint["entries"].values()))
            self.assertEqual(result["status"], "rate_limited")
            self.assertEqual(result["retry_after"], "30")

    def test_connectivity_check_reports_each_layer_without_downloading_bodies(self):
        response = Mock(status_code=204, headers={"Content-Type": "text/html"})
        response.close = Mock()
        with patch("render_connectivity._dns_probe", return_value={"status": "ok", "category": "ok", "address_count": 1}), \
             patch("render_connectivity._tcp_probe", return_value={"status": "ok", "category": "ok"}), \
             patch("render_connectivity.requests.Session.get", return_value=response) as get:
            result = render_connectivity.run_connectivity_check()
        self.assertFalse(result["body_downloaded"])
        self.assertEqual(len(result["endpoints"]), 4)
        self.assertEqual(result["endpoints"][0]["dns"]["status"], "ok")
        self.assertEqual(result["endpoints"][0]["tcp"]["status"], "ok")
        self.assertEqual(result["endpoints"][0]["https"]["http_status"], 204)
        self.assertEqual(get.call_count, 4)
        self.assertTrue(all(call.kwargs["stream"] for call in get.call_args_list))

    def test_connectivity_check_classifies_timeout_without_exception_details(self):
        with patch("render_connectivity._dns_probe", return_value={"status": "ok", "category": "ok", "address_count": 1}), \
             patch("render_connectivity._tcp_probe", return_value={"status": "failed", "category": "timeout", "error_type": "Timeout"}), \
             patch("render_connectivity.requests.Session.get", side_effect=requests.ReadTimeout("secret-proxy-value")):
            result = render_connectivity.run_connectivity_check()
        self.assertEqual(result["endpoints"][0]["tcp"]["category"], "timeout")
        self.assertEqual(result["endpoints"][0]["https"]["category"], "timeout")
        self.assertNotIn("secret-proxy-value", str(result))
    def test_connectivity_diagnostic_does_not_expose_proxy_details(self):
        diagnostic = refresh_pipeline.connectivity_diagnostic(
            ProxyError("https://user:secret@example.invalid tunnel failed")
        )
        self.assertEqual(diagnostic["category"], "proxy_or_egress")
        self.assertIn(diagnostic["proxy_configured"], {"yes", "no"})
        self.assertNotIn("secret", str(diagnostic))

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
            self.assertIn("ConnectTimeout", result["error_summaries"][0])

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
                    if unit_id == "15":
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

    def test_collect_udh_catalogue_fetches_all_discovered_schemes_and_deduplicates_ids(self):
        entries = [
            {"scheme_name": "One", "official_listing_url": "https://udhonline.rajasthan.gov.in/one", "reported_auction_count": 1},
            {"scheme_name": "Two", "official_listing_url": "https://udhonline.rajasthan.gov.in/two", "reported_auction_count": 1},
        ]
        records = [self._udh_record("1"), self._udh_record("1")]
        with tempfile.TemporaryDirectory() as directory:
            with patch("refresh_pipeline.discover_udh_entries", return_value=entries), \
                 patch("refresh_pipeline.fetch", side_effect=[("<one>", 200, False), ("<two>", 200, False)]), \
                 patch("refresh_pipeline.parse_page", side_effect=[
                     ([records[0]], "one"), ([records[1]], "two")
                 ]):
                result, details = refresh_pipeline.collect_udh_catalogue(
                    Mock(), retries=1, interval=0, cache_ttl=0, use_cache=False,
                    progress_file=Path(directory) / "progress.json",
                )
        self.assertEqual(len(result), 1)
        self.assertEqual(details["scheme_count"], 2)
        self.assertEqual(len(result[0]["source_provenance"]), 2)

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

    def test_rate_limit_stops_without_retrying_rate_limited_url(self):
        with tempfile.TemporaryDirectory() as directory:
            old_cache = refresh_pipeline.CACHE_DIR
            refresh_pipeline.CACHE_DIR = Path(directory)
            try:
                session = Mock()
                limited = Mock(status_code=429, headers={"Retry-After": "7"})
                session.get.side_effect = [limited]
                with patch("refresh_pipeline.time.sleep") as sleep:
                    with self.assertRaises(refresh_pipeline.RateLimitedError) as raised:
                        refresh_pipeline.fetch(
                        session, "https://example.test/rate", retries=1,
                        interval=0, cache_ttl=0, use_cache=False,
                        )
                self.assertEqual(raised.exception.retry_after, "7")
                self.assertEqual(session.get.call_count, 1)
                self.assertEqual(sleep.call_count, 0)
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
