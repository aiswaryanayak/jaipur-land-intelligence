import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["SITE_PASSWORD"] = "analysis-test-password"
import app


def property_item(auction_id="A-1"):
    return {
        "auction_id": auction_id,
        "title": "Test property",
        "scheme": "Swarn Vihar Scheme",
        "property_number": "168-B(Corner)",
        "area": "162.5 Square Meter",
        "usage": "Residential",
        "assessed_value": "12,000,000 (INR)",
        "source_authority": "Jaipur Development Authority",
        "detail_links": ["https://udhonline.rajasthan.gov.in/Portal/ViewAuction?id=1"],
        "listing_links": [],
    }


class PropertyAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        token = base64.b64encode(b"viewer:analysis-test-password").decode()
        self.headers = {"Authorization": f"Basic {token}"}

    def test_roi_calculator_and_annualization(self):
        result = app.calculate_roi(100, 20, 180, 2)
        self.assertEqual(result["total_cost"], 120)
        self.assertEqual(result["net_profit"], 60)
        self.assertAlmostEqual(result["roi_percent"], 50)
        self.assertAlmostEqual(result["annualized_roi_percent"], ((1.5 ** 0.5) - 1) * 100)

    def test_numeric_price_parses_comma_separated_amount(self):
        self.assertEqual(app.numeric_price("2,57,92,000.00 (INR)"), 25792000.0)

    def test_numeric_price_returns_none_for_invalid_or_empty_input(self):
        self.assertIsNone(app.numeric_price(""))
        self.assertIsNone(app.numeric_price("not a price"))

    def test_missing_history_is_honest(self):
        item = property_item()
        with patch.object(app, "resolve_property", return_value=item), patch.object(
            app, "load_property_history", return_value=[]
        ):
            response = self.client.get("/property/A-1/analysis", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("No verified property-specific historical observation", response.get_data(as_text=True))

    def test_post_auction_observation_has_source_attribution(self):
        item = property_item()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_text(json.dumps([{
                "source_authority": "Jaipur Development Authority",
                "source_url": "https://jdaservice.rajasthan.gov.in/post-auction",
                "auction_date": "08/10/2026",
                "scheme_name": "Swarn Vihar Scheme",
                "plot_number": "168-B(Corner)",
                "area_sqm": 162.5,
                "auctioned_rate_inr_per_sqm": 74000,
            }]), encoding="utf-8")
            with patch.object(app, "POST_AUCTION_FILE", path), patch.object(
                app, "latest_snapshot_file", return_value=None
            ), patch.object(app, "DATA_FILE", Path(directory) / "missing.json"):
                history = app.load_property_history("A-1", item)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["metric_type"], "auctioned rate")
        self.assertEqual(history[0]["unit"], "INR per square metre")
        self.assertEqual(history[0]["source_url"], "https://jdaservice.rajasthan.gov.in/post-auction")

    def test_duplicate_catalogue_observations_are_collapsed_by_source_identity(self):
        item = property_item("44739")
        records = []
        for detail_url, retrieved_at, source_id in (
            ("https://udhonline.rajasthan.gov.in/detail?a=1", "2026-10-13T00:00:00+00:00", "same-row"),
            ("https://udhonline.rajasthan.gov.in/detail?a=2", "2026-10-13T00:00:00+00:00", "same-row"),
            ("https://udhonline.rajasthan.gov.in/detail?a=3", "2026-10-14T00:00:00+00:00", "later-row"),
        ):
            records.append({
                "auction_id": "44739",
                "source_authority": "Jaipur Development Authority",
                "retrieved_at_utc": retrieved_at,
                "assessed_value": "25,254,240.00 (INR)",
                "detail_links": [detail_url],
                "raw_row_sha256": source_id,
            })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.json"
            path.write_text(json.dumps({"records": records}), encoding="utf-8")
            with patch.object(app, "POST_AUCTION_FILE", Path(directory) / "missing.json"), patch.object(
                app, "latest_snapshot_file", return_value=None
            ), patch.object(app, "DATA_FILE", path):
                history = app.load_property_history("44739", item)
        self.assertEqual(len(history), 2)
        self.assertEqual(
            [entry["date_label"] for entry in history],
            ["13 Oct 2026", "14 Oct 2026"],
        )

    def test_scheme_only_does_not_join_history(self):
        item = property_item()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_text(json.dumps([{
                "source_authority": "Jaipur Development Authority",
                "source_url": "https://jdaservice.rajasthan.gov.in/post-auction",
                "auction_date": "08/10/2026",
                "scheme_name": "Swarn Vihar Scheme",
                "plot_number": "DIFFERENT",
                "area_sqm": 162.5,
                "auctioned_rate_inr_per_sqm": 74000,
            }]), encoding="utf-8")
            with patch.object(app, "POST_AUCTION_FILE", path), patch.object(
                app, "latest_snapshot_file", return_value=None
            ), patch.object(app, "DATA_FILE", Path(directory) / "missing.json"):
                history = app.load_property_history("A-1", item)
        self.assertEqual(history, [])

    def test_comparables_filter_sort_deduplicate_and_preserve_source(self):
        item = property_item("44834")
        item.update({
            "scheme": "TRANSPORT NAGAR YOJANA",
            "property_number": "17",
            "area": "496.00 Square Meter",
            "usage": "Residential",
        })
        records = [
            {
                "source_authority": "Jaipur Development Authority",
                "source_url": "https://jdaservice.rajasthan.gov.in/post-auction",
                "zone_name": "Deputy Commissioner (Zone 13)",
                "scheme_name": "Transport Nagar",
                "plot_number": "A3/403",
                "area_sqm": 500,
                "property_category": "Residential",
                "auction_date": "10/10/2025",
                "auctioned_rate_inr_per_sqm": 80000,
                "record_fingerprint": "same-source-row",
                "raw_source_row": {"zone_name": "Deputy Commissioner (Zone 13)", "scheme": "Transport Nagar"},
            },
            {
                "source_authority": "Jaipur Development Authority",
                "source_url": "https://jdaservice.rajasthan.gov.in/post-auction",
                "zone_name": "Deputy Commissioner (Zone 13)",
                "scheme_name": "Transport Nagar",
                "plot_number": "A3/403",
                "area_sqm": 500,
                "property_category": "Residential",
                "auction_date": "10/10/2025",
                "auctioned_rate_inr_per_sqm": 80000,
                "record_fingerprint": "same-source-row",
                "raw_source_row": {"zone_name": "Deputy Commissioner (Zone 13)", "scheme": "Transport Nagar"},
            },
            {
                "source_authority": "Jaipur Development Authority",
                "source_url": "https://jdaservice.rajasthan.gov.in/post-auction",
                "zone_name": "Deputy Commissioner (Zone 13)",
                "scheme_name": "Transport Nagar",
                "plot_number": "A3/406",
                "area_sqm": 480,
                "property_category": "Commercial",
                "auction_date": "10/10/2024",
                "auctioned_rate_inr_per_sqm": 70000,
                "record_fingerprint": "different-source-row",
                "raw_source_row": {"zone_name": "Deputy Commissioner (Zone 13)", "scheme": "Transport Nagar"},
            },
            {
                "source_authority": "Jaipur Development Authority",
                "source_url": "https://jdaservice.rajasthan.gov.in/post-auction",
                "zone_name": "Deputy Commissioner (Zone 12)",
                "scheme_name": "Transport Nagar",
                "plot_number": "wrong-zone",
                "area_sqm": 496,
                "property_category": "Residential",
                "auction_date": "10/10/2023",
                "auctioned_rate_inr_per_sqm": 60000,
                "record_fingerprint": "wrong-zone",
            },
            {
                "source_authority": "Jaipur Development Authority",
                "source_url": "",
                "zone_name": "Deputy Commissioner (Zone 13)",
                "scheme_name": "Transport Nagar",
                "plot_number": "invalid",
                "area_sqm": 496,
                "property_category": "Residential",
                "auction_date": "10/10/2023",
                "auctioned_rate_inr_per_sqm": 60000,
                "record_fingerprint": "invalid",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comparables.json"
            path.write_text(json.dumps(records), encoding="utf-8")
            with patch.object(app, "POST_AUCTION_FILE", path):
                comparables = app.load_comparable_auctions(item)
        self.assertEqual([entry["plot_number"] for entry in comparables], ["A3/406", "A3/403"])
        self.assertEqual(comparables[0]["source_url"], "https://jdaservice.rajasthan.gov.in/post-auction")
        self.assertEqual(app.comparable_statistics(comparables)["count"], 2)

    def test_comparables_are_separate_from_target_history(self):
        item = property_item("44834")
        item.update({"scheme": "TRANSPORT NAGAR YOJANA", "area": "496 Square Meter"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comparables.json"
            path.write_text(json.dumps([{
                "source_authority": "Jaipur Development Authority",
                "source_url": "https://jdaservice.rajasthan.gov.in/post-auction",
                "zone_name": "Deputy Commissioner (Zone 13)",
                "scheme_name": "Transport Nagar",
                "plot_number": "17",
                "area_sqm": 496,
                "property_category": "Residential",
                "auction_date": "10/10/2025",
                "auctioned_rate_inr_per_sqm": 80000,
                "record_fingerprint": "comparable-only",
                "raw_source_row": {"zone_name": "Deputy Commissioner (Zone 13)", "scheme": "Transport Nagar"},
            }]), encoding="utf-8")
            with patch.object(app, "POST_AUCTION_FILE", path):
                comparables = app.load_comparable_auctions(item)
        self.assertEqual(len(comparables), 1)
        self.assertEqual(comparables[0]["plot_number"], "17")
        self.assertNotIn("auction_id", comparables[0])

    def test_comparable_audit_separates_unverified_locality_and_invalid_rows(self):
        item = property_item("44834")
        item.update({"scheme": "TRANSPORT NAGAR YOJANA", "area": "496 Square Meter"})
        records = [
            {
                "source_authority": "Jaipur Development Authority",
                "source_url": "https://jdaservice.rajasthan.gov.in/jdamicroportalproject/NoticeBoard/PostAuction",
                "zone_name": "Deputy Commissioner (Zone 13)",
                "scheme_name": "Transport Nagar",
                "plot_number": "verified",
                "area_sqm": 496,
                "property_category": "Commercial",
                "auction_date": "10/10/2025",
                "auctioned_rate_inr_per_sqm": 80000,
                "record_fingerprint": "verified",
                "raw_source_row": {
                    "zone_name": "Deputy Commissioner (Zone 13)",
                    "scheme": "Transport Nagar",
                },
            },
            {
                "zone_name": "Deputy Commissioner (Zone 13)",
                "scheme_name": "Transport Nagar",
                "plot_number": "unverified",
                "area_sqm": 496,
                "property_category": "Commercial",
                "auction_date": "10/10/2025",
                "auctioned_rate_inr_per_sqm": 80000,
                "record_fingerprint": "unverified",
                "raw_source_row": {"zone_name": "Deputy Commissioner (Zone 13)"},
            },
            {
                "zone_name": "Deputy Commissioner (Zone 12)",
                "scheme_name": "Transport Nagar",
                "plot_number": "unrelated",
                "area_sqm": 496,
                "property_category": "Commercial",
                "auction_date": "10/10/2025",
                "auctioned_rate_inr_per_sqm": 80000,
                "record_fingerprint": "unrelated",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.json"
            path.write_text(json.dumps(records), encoding="utf-8")
            with patch.object(app, "POST_AUCTION_FILE", path):
                audit = app.audit_comparable_auctions(item)
        self.assertEqual(len(audit["included"]), 1)
        self.assertEqual(audit["same_zone_unverified"], 1)
        self.assertEqual(audit["excluded"], 1)

    def test_comparables_support_exact_locality_and_reviewed_aliases(self):
        item = property_item("S-1")
        item.update({"scheme": "Abhinav Vihar Vistar", "zone_name": "Zone 14"})
        base = {
            "source_authority": "Jaipur Development Authority",
            "source_url": "https://jdaservice.rajasthan.gov.in/jdamicroportalproject/NoticeBoard/PostAuction",
            "zone_name": "Deputy Commissioner (Zone 14)",
            "plot_number": "K-5",
            "property_category": "Residential",
            "area_sqm": 162,
            "auction_date": "10/10/2025",
            "auctioned_rate_inr_per_sqm": 32700,
        }
        records = [
            {
                **base,
                "scheme_name": "Abhinav Vihar Vistar",
                "record_fingerprint": "exact",
                "raw_source_row": {
                    "zone_name": "Deputy Commissioner (Zone 14)",
                    "scheme": "Abhinav Vihar Vistar",
                },
            },
            {
                **base,
                "scheme_name": "Different Locality",
                "record_fingerprint": "different",
                "raw_source_row": {
                    "zone_name": "Deputy Commissioner (Zone 14)",
                    "scheme": "Different Locality",
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scheme-variants.json"
            path.write_text(json.dumps(records), encoding="utf-8")
            with patch.object(app, "POST_AUCTION_FILE", path):
                audit = app.audit_comparable_auctions(item)
        self.assertEqual([entry["plot_number"] for entry in audit["included"]], ["K-5"])
        self.assertEqual(audit["excluded"], 1)
        self.assertEqual(
            audit["included"][0]["source_url"],
            base["source_url"],
        )

    def test_transport_alias_does_not_accept_wrong_zone_when_target_zone_is_known(self):
        item = property_item("T-1")
        item.update({
            "scheme": "TRANSPORT NAGAR YOJANA",
            "zone_name": "Zone 13",
        })
        record = {
            "source_authority": "Jaipur Development Authority",
            "source_url": "https://jdaservice.rajasthan.gov.in/jdamicroportalproject/NoticeBoard/PostAuction",
            "zone_name": "Deputy Commissioner (Zone 12)",
            "scheme_name": "Transport Nagar",
            "plot_number": "wrong-zone",
            "property_category": "Commercial",
            "area_sqm": 496,
            "auction_date": "10/10/2025",
            "auctioned_rate_inr_per_sqm": 80000,
            "record_fingerprint": "wrong-zone",
            "raw_source_row": {
                "zone_name": "Deputy Commissioner (Zone 12)",
                "scheme": "Transport Nagar",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong-zone.json"
            path.write_text(json.dumps([record]), encoding="utf-8")
            with patch.object(app, "POST_AUCTION_FILE", path):
                audit = app.audit_comparable_auctions(item)
        self.assertEqual(audit["included"], [])
        self.assertEqual(audit["excluded"], 1)

    def test_transport_catalogue_phase_alias_accepts_official_locality_rows(self):
        item = property_item("T-A2")
        item.update({
            "scheme": "TRANSPORT NAGAR YOJANA - A2",
            "zone_name": "Zone 13",
        })
        record = {
            "source_authority": "Jaipur Development Authority",
            "source_url": "https://jdaservice.rajasthan.gov.in/jdamicroportalproject/NoticeBoard/PostAuction",
            "zone_name": "Deputy Commissioner (Zone 13)",
            "scheme_name": "Transport Nagar",
            "plot_number": "A3/406",
            "property_category": "Commercial",
            "area_sqm": 1611,
            "auction_date": "24/08/2026",
            "auctioned_rate_inr_per_sqm": 48100,
            "record_fingerprint": "transport-phase-match",
            "raw_source_row": {
                "zone_name": "Deputy Commissioner (Zone 13)",
                "scheme": "Transport Nagar",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transport-phase.json"
            path.write_text(json.dumps([record]), encoding="utf-8")
            with patch.object(app, "POST_AUCTION_FILE", path):
                audit = app.audit_comparable_auctions(item)
        self.assertEqual([entry["plot_number"] for entry in audit["included"]], ["A3/406"])

    def test_comparable_audit_rejects_missing_dates_and_invalid_amounts(self):
        item = property_item("T-2")
        item.update({"scheme": "TRANSPORT NAGAR YOJANA", "zone_name": "Zone 13"})
        base = {
            "source_authority": "Jaipur Development Authority",
            "source_url": "https://jdaservice.rajasthan.gov.in/post-auction",
            "zone_name": "Deputy Commissioner (Zone 13)",
            "scheme_name": "Transport Nagar",
            "property_category": "Commercial",
            "area_sqm": 496,
            "raw_source_row": {
                "zone_name": "Deputy Commissioner (Zone 13)",
                "scheme": "Transport Nagar",
            },
        }
        records = [
            {**base, "plot_number": "no-date", "auction_date": "",
             "auctioned_rate_inr_per_sqm": 80000, "record_fingerprint": "no-date"},
            {**base, "plot_number": "bad-rate", "auction_date": "10/10/2025",
             "auctioned_rate_inr_per_sqm": 0, "record_fingerprint": "bad-rate"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-comparables.json"
            path.write_text(json.dumps(records), encoding="utf-8")
            with patch.object(app, "POST_AUCTION_FILE", path):
                audit = app.audit_comparable_auctions(item)
        self.assertEqual(audit["included"], [])
        self.assertEqual(audit["excluded"], 2)

    def test_comparable_chart_is_unconnected_readable_scatter(self):
        entries = [
            {
                "date": "2026-01-01T00:00:00+00:00",
                "date_label": "01 Jan 2026",
                "amount": 50000,
                "amount_label": "50,000.00",
                "plot_number": "A",
                "area_label": "100.00 sqm",
                "category": "Commercial",
                "authority": "JDA",
                "source_url": "https://jda.example/source-a",
            },
            {
                "date": "2026-01-01T00:00:00+00:00",
                "date_label": "01 Jan 2026",
                "amount": 50000,
                "amount_label": "50,000.00",
                "plot_number": "B",
                "area_label": "100.00 sqm",
                "category": "Commercial",
                "authority": "JDA",
                "source_url": "https://jda.example/source-b",
            },
            {
                "date": "2026-02-01T00:00:00+00:00",
                "date_label": "01 Feb 2026",
                "amount": 250000,
                "amount_label": "250,000.00",
                "plot_number": "C",
                "area_label": "100.00 sqm",
                "category": "Commercial",
                "authority": "JDA",
                "source_url": "https://jda.example/source-c",
            },
        ]
        series = app._comparable_chart_series(entries)[0]
        self.assertNotIn("polyline", series)
        self.assertEqual(len(series["points"]), 3)
        self.assertNotEqual(
            (series["points"][0]["x"], series["points"][0]["y"]),
            (series["points"][1]["x"], series["points"][1]["y"]),
        )
        self.assertEqual(series["tier"], "Verified same locality")
        self.assertTrue(series["x_ticks"])
        self.assertTrue(series["y_ticks"])

    def test_property_analysis_isolated_by_identifier(self):
        first = property_item("A-1")
        second = property_item("A-2")
        observed = {
            "date": "2026-01-01T00:00:00+00:00",
            "date_label": "01 Jan 2026",
            "amount": 100,
            "amount_label": "100.00",
            "metric_type": "auctioned rate",
            "unit": "INR per square metre",
            "authority": "JDA",
            "source_url": "https://jda.rajasthan.gov.in/source",
            "identity_note": "Exact synthetic match.",
        }
        with patch.object(app, "resolve_property", side_effect=[first, second]), patch.object(
            app, "load_property_history", side_effect=[[observed], []]
        ):
            first_response = self.client.get("/property/A-1/analysis", headers=self.headers)
            second_response = self.client.get("/property/A-2/analysis", headers=self.headers)
        self.assertIn("01 Jan 2026", first_response.get_data(as_text=True))
        self.assertNotIn("01 Jan 2026", second_response.get_data(as_text=True))

    def test_missing_gemini_configuration_is_graceful(self):
        item = property_item()
        with patch.object(app, "resolve_property", return_value=item), patch.object(
            app, "load_property_history", return_value=[]
        ), patch.dict(os.environ, {}, clear=True):
            response = self.client.post(
                "/property/A-1/analysis/chat",
                data={"question": "What is known about this property?"},
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("GEMINI_API_KEY", response.get_data(as_text=True))

    def test_gemini_prompt_includes_catalogue_matches(self):
        fake_response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "candidates": [{"content": {"parts": [{"text": "grounded answer"}]}}]
            },
        })()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True), \
                patch.object(
                    app,
                    "_catalogue_matches",
                    return_value=([(
                        2,
                        {
                            "auction_id": "A-2",
                            "title": "Other property",
                            "scheme": "Other Scheme",
                            "property_number": "22",
                            "area": "162 Square Meter",
                            "usage": "Residential",
                            "assessed_value": "1000000 (INR)",
                            "detail_links": [
                                "https://udhonline.rajasthan.gov.in/property/A-2"
                            ],
                            "source_authority": "Jaipur Development Authority",
                        },
                    )], "catalogue timestamp"),
                ), \
                patch.object(app.requests, "post", return_value=fake_response) as post:
            answer, error = app._gemini_answer(
                property_item(), [], [], None, {}, "Compare this property."
            )
        self.assertEqual(answer, "grounded answer")
        self.assertIsNone(error)
        prompt = post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]
        self.assertIn('"auction_id": "A-2"', prompt)
        self.assertIn("catalogue_matches", prompt)

    def test_gemini_model_is_configurable_in_request_url(self):
        fake_response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
            },
        })()
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "gemini-2.5-flash-lite"},
            clear=True,
        ), patch.object(
            app, "_catalogue_matches", return_value=([], "timestamp")
        ), patch.object(
            app.requests, "post", return_value=fake_response
        ) as post:
            answer, error = app._gemini_answer(
                property_item(), [], [], None, {}, "What is known?"
            )
        self.assertEqual(answer, "ok")
        self.assertIsNone(error)
        self.assertEqual(
            post.call_args.args[0],
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash-lite:generateContent",
        )

    def test_gemini_profitability_prompt_uses_roi_guidance_and_missing_inputs(self):
        fake_response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "candidates": [{"content": {"parts": [{"text": "grounded answer"}]}}]
            },
        })()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True), \
                patch.object(app, "_catalogue_matches", return_value=([], "timestamp")), \
                patch.object(app.requests, "post", return_value=fake_response) as post:
            answer, error = app._gemini_answer(
                property_item(), [], [], None,
                {"acquisition_price": 12000000, "additional_costs": 500000,
                 "expected_resale_value": None, "holding_years": None},
                "Is this property potentially profitable?"
            )
        self.assertEqual(answer, "grounded answer")
        self.assertIsNone(error)
        prompt = post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]
        self.assertIn("expected resale value", prompt)
        self.assertIn("Do not answer with a disclaimer alone", prompt)
        self.assertIn("conditional evidence-based conclusion", prompt)

    def test_gemini_model_configuration_rejects_invalid_path(self):
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "models/bad"},
            clear=True,
        ):
            answer, error = app._gemini_answer(
                property_item(), [], [], None, {}, "What is known?"
            )
        self.assertIsNone(answer)
        self.assertIn("configuration is invalid", error)

    def test_empty_catalogue_matches_are_preserved(self):
        fake_response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "candidates": [{"content": {"parts": [{"text": "no matches"}]}}]
            },
        })()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True), \
                patch.object(
                    app,
                    "_catalogue_matches",
                    return_value=([], "catalogue timestamp"),
                ), \
                patch.object(app.requests, "post", return_value=fake_response) as post:
            answer, error = app._gemini_answer(
                property_item(), [], [], None, {}, "Find a matching property."
            )
        self.assertEqual(answer, "no matches")
        self.assertIsNone(error)
        prompt = post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]
        self.assertIn('"catalogue_matches": []', prompt)

    def test_gemini_api_failure_is_graceful(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True), \
                patch.object(
                    app, "_catalogue_matches",
                    return_value=([], "catalogue timestamp"),
                ), \
                patch.object(
                    app.requests,
                    "post",
                    side_effect=app.requests.RequestException("timeout"),
                ):
            answer, error = app._gemini_answer(
                property_item(), [], [], None, {}, "What should I verify?"
            )
        self.assertIsNone(answer)
        self.assertIn("could not answer", error)

    def test_malformed_gemini_responses_are_graceful(self):
        payloads = [
            {},
            {"candidates": []},
            {"candidates": [{}]},
            {"candidates": [{"content": {"parts": []}}]},
            {"candidates": [{"content": {"parts": [{"text": 123}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "  "}]}}]},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), \
                    patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True), \
                    patch.object(
                        app, "_catalogue_matches",
                        return_value=([], "catalogue timestamp"),
                    ):
                fake_response = type("Response", (), {
                    "raise_for_status": lambda self: None,
                    "json": lambda self, payload=payload: payload,
                })()
                with patch.object(app.requests, "post", return_value=fake_response):
                    answer, error = app._gemini_answer(
                        property_item(), [], [], None, {}, "What is known?"
                    )
            self.assertIsNone(answer)
            self.assertIn("could not answer", error)

    def test_analysis_requires_existing_authentication(self):
        response = self.client.get("/property/A-1/analysis")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
