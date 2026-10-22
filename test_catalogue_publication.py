import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import catalogue_publication as publication

AS_OF = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def manifest(
    source_key,
    count=1,
    coverage_complete=True,
    coverage_evidence=None,
):
    if coverage_evidence is None:
        if source_key == "riico-eauction":
            coverage_evidence = {
                "all_scope_checked": True,
                "observed_record_count": count,
                "unit_record_counts": {"jaipur-north": count},
            }
        elif source_key == "jda-post-auction":
            coverage_evidence = {
                "all_scope_checked": True,
                "observed_record_count": count,
                "page_record_counts": [count],
            }
        else:
            coverage_evidence = {
                "all_scope_checked": True,
                "observed_record_count": count,
                "listed_id_count": count,
                "detail_record_count": count,
                "detail_errors": [],
            }
    return {
        "run_id": "20261020T000000Z",
        "promotion_performed": False,
        "sources": [{
            "source_key": source_key,
            "fetch_status": "success",
            "record_count": count,
            "error": None,
            "coverage_complete": coverage_complete,
            "coverage_evidence": coverage_evidence,
        }],
    }


def riico_record(identifier="42", status="active", review_status="approved_for_catalogue"):
    return {
        "authority": "RIICO",
        "source_type": "e-auction",
        "source_url": "https://riico.example.gov/auction",
        "original_identifier": identifier,
        "title": "RIICO plot",
        "property_or_plot_number": "A-1",
        "scheme_or_industrial_area": "Jaipur North",
        "land_use_category": "Industrial",
        "area": "10 Sqm",
        "reserve_price_or_rate": "100 Rs. Per Sqm",
        "emd": "10",
        "application_start": "01-Nov-2026 10:00 AM",
        "application_end": "02-Nov-2026 10:00 AM",
        "bid_start": "03-Nov-2026 10:00 AM",
        "bid_end": "04-Nov-2026 10:00 AM",
        "current_status": status,
        "last_checked_at": "2026-10-20T00:00:00+05:30",
        "review_status": review_status,
        "notes": "Source row reviewed.",
    }


def jda_record(identifier="abc", source_type="active_auction_listing", status="active"):
    return {
        "source_authority": "Jaipur Development Authority",
        "source_record_type": source_type,
        "source_url": "https://udhonline.rajasthan.gov.in/Portal/ViewAuction?id=1",
        "auction_id": identifier,
        "title": "JDA plot",
        "scheme_name": "Test Scheme",
        "property_number": "P-1",
        "property_area": "100 Sqm",
        "usage_type": "Residential",
        "emd_amount": "1000",
        "emd_start": "01-Nov-2026 10:00 AM",
        "emd_end": "02-Nov-2026 10:00 AM",
        "bid_start": "03-Nov-2026 10:00 AM",
        "bid_end": "04-Nov-2026 10:00 AM",
        "status": status,
        "retrieved_at_utc": "2026-10-20T00:00:00+00:00",
        "review_status": "approved_for_catalogue",
    }


class CataloguePublicationTests(unittest.TestCase):
    def test_normalizes_jda_and_riico_records(self):
        jda_run, jda = publication.normalize_candidate(
            manifest("jda-post-auction"),
            {"jda-post-auction": [jda_record()]},
            as_of_utc=AS_OF,
        )
        riico_run, riico = publication.normalize_candidate(
            manifest("riico-eauction"),
            {"riico-eauction": [riico_record()]},
            as_of_utc=AS_OF,
        )
        self.assertEqual(jda_run, riico_run)
        self.assertEqual(jda[0].canonical_id, "JDA:abc")
        self.assertEqual(riico[0].canonical_id, "RIICO:42")
        self.assertEqual(riico[0].classification, "active")

    def test_historical_classification_is_preserved(self):
        _, records = publication.normalize_candidate(
            manifest("jda-post-auction"),
            {"jda-post-auction": [jda_record(
                source_type="post_auction_result",
                status="completed",
            )]},
            as_of_utc=AS_OF,
        )
        self.assertEqual(records[0].classification, "historical")

    def test_status_false_positives_are_rejected(self):
        with self.assertRaises(publication.PublicationValidationError):
            publication.normalize_candidate(
                manifest("riico-eauction"),
                {"riico-eauction": [riico_record(status="completedness")]},
                as_of_utc=AS_OF,
            )
        _, records = publication.normalize_candidate(
            manifest("riico-eauction"),
            {"riico-eauction": [riico_record() | {
                "source_type": "nonhistorical_source"
            }]},
            as_of_utc=AS_OF,
        )
        self.assertEqual(records[0].classification, "active")

    def test_timestamp_formats_and_timezone_rules(self):
        self.assertEqual(
            publication._timestamp("2026-11-04T10:00:00Z", "date"),
            datetime(2026, 11, 4, 10, tzinfo=timezone.utc),
        )
        self.assertEqual(
            publication._timestamp("2026-11-04T10:00:00+05:30", "date"),
            datetime(2026, 11, 4, 4, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            publication._timestamp("04-Nov-2026 10:00 AM", "date"),
            datetime(2026, 11, 4, 4, 30, tzinfo=timezone.utc),
        )
        for value in ("2026-11-04T10:00:00", "2026-11-04"):
            with self.subTest(value=value):
                with self.assertRaises(publication.PublicationValidationError):
                    publication._timestamp(value, "date")
        with self.assertRaises(publication.PublicationValidationError):
            publication.normalize_candidate(
                manifest("riico-eauction"),
                {"riico-eauction": [riico_record()]},
                as_of_utc=datetime(2026, 10, 20, 12),
            )

    def test_invalid_records_are_rejected(self):
        cases = [
            ("missing approval", {**riico_record(), "review_status": "manual_review"}),
            ("bad source URL", {**riico_record(), "source_url": "http://example.gov"}),
            ("bad classification", {**riico_record(), "current_status": "unknown"}),
            ("missing stable ID", {**riico_record(), "original_identifier": ""}),
        ]
        for label, record in cases:
            with self.subTest(label=label):
                with self.assertRaises(publication.PublicationValidationError):
                    publication.normalize_candidate(
                        manifest("riico-eauction"),
                        {"riico-eauction": [record]},
                        as_of_utc=AS_OF,
                    )

    def test_duplicate_authority_qualified_ids_are_rejected(self):
        with self.assertRaisesRegex(publication.PublicationValidationError, "Duplicate"):
            publication.normalize_candidate(
                manifest("riico-eauction", count=2),
                {"riico-eauction": [riico_record(), riico_record()]},
                as_of_utc=AS_OF,
            )

    def test_incomplete_refresh_fails_closed(self):
        with self.assertRaisesRegex(publication.PublicationValidationError, "coverage_complete"):
            publication.normalize_candidate(
                manifest("riico-eauction", coverage_complete=False),
                {"riico-eauction": [riico_record()]},
                as_of_utc=AS_OF,
            )

    def test_expired_opportunity_is_not_active(self):
        record = riico_record()
        record["bid_end"] = "19-Oct-2026 11:00 PM"
        _, records = publication.normalize_candidate(
            manifest("riico-eauction"),
            {"riico-eauction": [record]},
            as_of_utc=AS_OF,
        )
        self.assertEqual(records[0].classification, "historical")
        self.assertEqual(records[0].status, "expired")

    def test_newer_closure_overrides_older_active(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "catalogue.json"
            publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [riico_record()]},
                destination,
                as_of_utc=AS_OF,
            )
            closed = riico_record(status="closed")
            closed["last_checked_at"] = "2026-10-20T11:00:00+05:30"
            publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [closed]},
                destination,
                as_of_utc=AS_OF,
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(payload["records"][0]["classification"], "historical")
            self.assertEqual(payload["records"][0]["status"], "closed")

    def test_equal_time_active_does_not_reopen_historical_records(self):
        for status in ("closed", "cancelled", "expired"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "catalogue.json"
                historical = riico_record(status=status)
                publication.publish(
                    manifest("riico-eauction"),
                    {"riico-eauction": [historical]},
                    destination,
                    as_of_utc=AS_OF,
                )
                active = riico_record()
                publication.publish(
                    manifest("riico-eauction"),
                    {"riico-eauction": [active]},
                    destination,
                    as_of_utc=AS_OF,
                )
                payload = json.loads(destination.read_text(encoding="utf-8"))
                self.assertEqual(payload["records"][0]["status"], status)
                self.assertEqual(payload["records"][0]["classification"], "historical")

    def test_strictly_newer_active_replaces_older_active_record(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "catalogue.json"
            older = riico_record()
            publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [older]},
                destination,
                as_of_utc=AS_OF,
            )
            newer = riico_record(status="active")
            newer["last_checked_at"] = "2026-10-20T12:01:00+05:30"
            publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [newer]},
                destination,
                as_of_utc=AS_OF,
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(payload["records"][0]["status"], "active")

    def test_retained_active_record_expires_during_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "catalogue.json"
            record = riico_record()
            record["bid_end"] = "19-Oct-2026 11:00 PM"
            publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [record]},
                destination,
                as_of_utc=datetime(2026, 10, 19, 12, tzinfo=timezone.utc),
            )
            publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [riico_record("other")]},
                destination,
                as_of_utc=AS_OF,
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(payload["records"][0]["classification"], "historical")
            self.assertEqual(payload["records"][0]["status"], "expired")

    def test_invalid_retained_active_deadline_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "catalogue.json"
            publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [riico_record()]},
                destination,
                as_of_utc=AS_OF,
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))
            payload["records"][0]["bid_end"] = ""
            before = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
            destination.write_bytes(before)
            with self.assertRaises(publication.PublicationValidationError):
                publication.publish(
                    manifest("riico-eauction"),
                    {"riico-eauction": [riico_record("other")]},
                    destination,
                    as_of_utc=AS_OF,
                )
            self.assertEqual(destination.read_bytes(), before)

    def test_older_active_does_not_overwrite_newer_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "catalogue.json"
            closed = riico_record(status="closed")
            closed["last_checked_at"] = "2026-10-20T11:00:00+05:30"
            publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [closed]},
                destination,
                as_of_utc=AS_OF,
            )
            older = riico_record()
            older["last_checked_at"] = "2026-10-20T10:00:00+05:30"
            publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [older]},
                destination,
                as_of_utc=AS_OF,
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(payload["records"][0]["classification"], "historical")
            self.assertEqual(payload["records"][0]["status"], "closed")

    def test_partial_refresh_retains_missing_record(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "catalogue.json"
            publication.publish(
                manifest("riico-eauction", count=2),
                {"riico-eauction": [riico_record("42"), riico_record("43")]},
                destination,
                as_of_utc=AS_OF,
            )
            publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [riico_record("42")]},
                destination,
                as_of_utc=AS_OF,
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(
                {row["canonical_id"] for row in payload["records"]},
                {"RIICO:42", "RIICO:43"},
            )

    def test_ambiguous_status_or_invalid_date_is_rejected(self):
        cases = [
            {**riico_record(), "current_status": "possibly active"},
            {**riico_record(), "bid_end": "2026-11-04"},
            {**riico_record(), "bid_end": "not a date"},
        ]
        for record in cases:
            with self.subTest(record=record):
                with self.assertRaises(publication.PublicationValidationError):
                    publication.normalize_candidate(
                        manifest("riico-eauction"),
                        {"riico-eauction": [record]},
                        as_of_utc=AS_OF,
                    )

    def test_completeness_evidence_is_required_and_reconciled(self):
        missing = manifest("riico-eauction")
        del missing["sources"][0]["coverage_evidence"]
        contradictory = manifest("riico-eauction")
        contradictory["sources"][0]["coverage_evidence"]["unit_record_counts"] = {
            "jaipur-north": 2
        }
        for candidate in (missing, contradictory):
            with self.subTest(candidate=candidate):
                with self.assertRaises(publication.PublicationValidationError):
                    publication.normalize_candidate(
                        candidate,
                        {"riico-eauction": [riico_record()]},
                        as_of_utc=AS_OF,
                    )

    def test_jda_completeness_evidence_is_required_and_reconciled(self):
        missing = manifest("jda-post-auction")
        del missing["sources"][0]["coverage_evidence"]
        contradictory = manifest("jda-post-auction")
        contradictory["sources"][0]["coverage_evidence"]["page_record_counts"] = [2]
        invalid_type = manifest("jda-post-auction")
        invalid_type["sources"][0]["coverage_evidence"]["page_record_counts"] = [True]
        for candidate in (missing, contradictory, invalid_type):
            with self.subTest(candidate=candidate):
                with self.assertRaises(publication.PublicationValidationError):
                    publication.normalize_candidate(
                        candidate,
                        {"jda-post-auction": [jda_record()]},
                        as_of_utc=AS_OF,
                    )

    def test_udh_completeness_evidence_is_required_and_reconciled(self):
        mismatched = manifest("udh-jda-live-auctions")
        mismatched["sources"][0]["coverage_evidence"]["listed_id_count"] = 2
        detail_mismatch = manifest("udh-jda-live-auctions")
        detail_mismatch["sources"][0]["coverage_evidence"]["detail_record_count"] = 2
        detail_error = manifest("udh-jda-live-auctions")
        detail_error["sources"][0]["coverage_evidence"]["detail_errors"] = ["429"]
        invalid_type = manifest("udh-jda-live-auctions")
        invalid_type["sources"][0]["coverage_evidence"]["listed_id_count"] = True
        for candidate in (mismatched, detail_mismatch, detail_error, invalid_type):
            with self.subTest(candidate=candidate):
                with self.assertRaises(publication.PublicationValidationError):
                    publication.normalize_candidate(
                        candidate,
                        {"udh-jda-live-auctions": [{"auction_id": "1"}]},
                        as_of_utc=AS_OF,
                    )

    def test_count_mismatch_fails_closed(self):
        with self.assertRaises(publication.PublicationValidationError):
            publication.normalize_candidate(
                manifest("riico-eauction", count=2),
                {"riico-eauction": [riico_record()]},
                as_of_utc=AS_OF,
            )

    def test_publication_retains_records_absent_from_one_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "catalogue.json"
            first = publication.publish(
                manifest("riico-eauction", count=2),
                {"riico-eauction": [
                    riico_record("42"),
                    riico_record("43"),
                ]},
                destination,
                as_of_utc=AS_OF,
            )
            second = publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [riico_record("42", status="upcoming")]},
                destination,
                as_of_utc=AS_OF,
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(first["published_count"], 2)
            self.assertEqual(second["published_count"], 2)
            self.assertEqual(
                {row["canonical_id"] for row in payload["records"]},
                {"RIICO:42", "RIICO:43"},
            )

    def test_failed_atomic_replacement_retains_old_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "catalogue.json"
            publication.publish(
                manifest("riico-eauction"),
                {"riico-eauction": [riico_record()]},
                destination,
                as_of_utc=AS_OF,
            )
            before = destination.read_bytes()
            with patch.object(publication.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(publication.PublicationValidationError):
                    publication.publish(
                        manifest("riico-eauction"),
                        {"riico-eauction": [riico_record(status="upcoming")]},
                        destination,
                        as_of_utc=AS_OF,
                    )
            self.assertEqual(destination.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
