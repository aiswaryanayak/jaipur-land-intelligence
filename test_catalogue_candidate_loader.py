import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import catalogue_candidate_loader as loader
from catalogue_publication import PublicationValidationError


AS_OF = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def riico_record(identifier="42", review_status="approved_for_catalogue"):
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
        "current_status": "active",
        "last_checked_at": "2026-10-20T00:00:00+00:00",
        "review_status": review_status,
    }


def write_fixture(
    root: Path,
    *,
    records=None,
    run_id="run-1",
    source_key="riico-eauction",
    manifest_overrides=None,
    staged_run_id=None,
):
    records = [riico_record()] if records is None else records
    stage_key = loader.SOURCE_STAGE_KEYS.get(source_key, "unknown_source")
    stage_path = root / "runs" / "records.json"
    stage_path.parent.mkdir(parents=True)
    stage_path.write_text(json.dumps({
        "run_id": staged_run_id or run_id,
        "source_key": source_key,
        "records": records,
    }), encoding="utf-8")
    source = {
        "source_key": source_key,
        "run_id": run_id,
        "fetch_status": "success",
        "record_count": len(records),
        "error": None,
        "coverage_complete": True,
        "coverage_evidence": {
            "all_scope_checked": True,
            "observed_record_count": len(records),
            "unit_record_counts": {"jaipur-north": len(records)},
        },
    }
    manifest = {
        "run_id": run_id,
        "promotion_performed": False,
        "sources": [source],
        "staged_files": {stage_key: str(stage_path.relative_to(root))},
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    manifest_path = root / "runs" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


class CatalogueCandidateLoaderTests(unittest.TestCase):
    def test_loads_selected_run_and_maps_homepage_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(root)
            candidate = loader.load_candidate(
                manifest_path, repository_root=root, as_of_utc=AS_OF
            )
            mapped = loader.to_homepage_records(candidate)
            self.assertEqual(candidate.run_id, "run-1")
            self.assertEqual(mapped[0]["auction_id"], "RIICO:42")
            self.assertEqual(mapped[0]["scheme"], "Jaipur North")
            self.assertEqual(mapped[0]["property_number"], "A-1")
            self.assertEqual(mapped[0]["status_class"], "future")
            self.assertEqual(mapped[0]["listing_links"], [
                "https://riico.example.gov/auction"
            ])
            self.assertEqual(mapped[0]["detail_links"], [])

    def test_missing_staged_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(root)
            (root / "runs" / "records.json").unlink()
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_malformed_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(root)
            (root / "runs" / "records.json").write_text("{", encoding="utf-8")
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_run_id_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(root, staged_run_id="run-2")
            with self.assertRaisesRegex(PublicationValidationError, "run_id mismatch"):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_unsupported_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(root, source_key="unknown-source")
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_incomplete_coverage_is_rejected_by_publication_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(
                root,
                manifest_overrides={
                    "sources": [{
                        "source_key": "riico-eauction",
                        "run_id": "run-1",
                        "fetch_status": "success",
                        "record_count": 1,
                        "coverage_complete": True,
                    }]
                },
            )
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_unapproved_record_is_not_converted_to_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(
                root, records=[riico_record(review_status="verified_official_listing")]
            )
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_duplicate_identifiers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(
                root, records=[riico_record(), riico_record()]
            )
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_missing_staged_run_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(root)
            (root / "runs" / "records.json").write_text(
                json.dumps({
                    "source_key": "riico-eauction",
                    "records": [riico_record()],
                }), encoding="utf-8"
            )
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_manifest_outside_repository_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            manifest_path = write_fixture(outside)
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_manifest_parent_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            manifest_path = write_fixture(outside)
            relative_path = root / ".." / "outside" / "runs" / "manifest.json"
            self.assertEqual(manifest_path, relative_path.resolve())
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    relative_path, repository_root=root, as_of_utc=AS_OF
                )

    @unittest.skipUnless(os.name == "nt", "Windows-specific path syntax")
    def test_windows_absolute_and_unc_manifest_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in (
                Path(r"C:\outside\manifest.json"),
                Path(r"\\server\share\manifest.json"),
            ):
                with self.subTest(path=path):
                    with self.assertRaises(PublicationValidationError):
                        loader.load_candidate(
                            path, repository_root=root, as_of_utc=AS_OF
                        )

    def test_symlink_manifest_escape_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            manifest_path = write_fixture(outside)
            link = root / "manifest.json"
            try:
                link.symlink_to(manifest_path)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not supported")
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    link, repository_root=root, as_of_utc=AS_OF
                )

    def test_missing_staged_source_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(root)
            staged_path = root / "runs" / "records.json"
            payload = json.loads(staged_path.read_text(encoding="utf-8"))
            del payload["source_key"]
            staged_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_staged_source_key_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(root)
            staged_path = root / "runs" / "records.json"
            payload = json.loads(staged_path.read_text(encoding="utf-8"))
            payload["source_key"] = "jda-post-auction"
            staged_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(PublicationValidationError):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_duplicate_manifest_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(root)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["sources"].append(dict(payload["sources"][0]))
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                PublicationValidationError, "duplicate source_key"
            ):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

    def test_one_staged_file_cannot_be_reused_by_two_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_fixture(root)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["sources"].append({
                "source_key": "jda-post-auction",
                "run_id": "run-1",
                "fetch_status": "success",
                "record_count": 1,
                "error": None,
                "coverage_complete": True,
                "coverage_evidence": {
                    "all_scope_checked": True,
                    "observed_record_count": 1,
                    "page_record_counts": [1],
                },
            })
            payload["staged_files"]["jda_post_auction"] = (
                payload["staged_files"]["riico_eauction"]
            )
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                PublicationValidationError, "reused by multiple sources"
            ):
                loader.load_candidate(
                    manifest_path, repository_root=root, as_of_utc=AS_OF
                )

if __name__ == "__main__":
    unittest.main()
