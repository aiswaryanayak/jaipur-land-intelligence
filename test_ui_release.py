import base64
import sqlite3
import tempfile
import os
import unittest
from unittest.mock import patch

os.environ["SITE_PASSWORD"] = "test-password"
import app


def item(auction_id, price, category="commercial", deadline="21-Oct-2026 05:00 PM"):
    return {
        "auction_id": str(auction_id),
        "title": f"Plot {auction_id}",
        "scheme": "Test Scheme",
        "property_number": str(auction_id),
        "area": "100 Sqm",
        "usage": "Commercial",
        "emd": "1000",
        "assessed_value": "" if price is None else f"{price:,.2f} (INR)",
        "emd_start": "01-Oct-2026 10:00 AM",
        "emd_end": "20-Oct-2026 10:00 AM",
        "bid_start": "01-Oct-2026 10:00 AM",
        "bid_end": deadline,
        "category": category,
        "category_label": "Commercial",
        "deadline_status": "Future published deadline — official status unverified",
        "status_class": "future",
        "listing_links": [],
        "detail_links": [],
        "provenance_count": 1,
        "raw_text": "",
        "source_authority": "Jaipur Development Authority",
        "_bid_dt": app.parse_date(deadline),
        "_price": price,
    }


class ReleaseUiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        token = base64.b64encode(b"viewer:test-password").decode()
        self.headers = {"Authorization": f"Basic {token}"}
        self.items = [item("low", 100), item("high", 1000), item("missing", None)]

    def test_price_minimum_and_maximum_filter(self):
        with patch.object(app, "load_catalogue", return_value=(self.items, "now")):
            response = self.client.get("/?min_price=200&max_price=1100", headers=self.headers)
        body = response.get_data(as_text=True)
        self.assertNotIn("Plot low", body)
        self.assertIn("Plot high", body)
        self.assertNotIn("Plot missing", body)

    def test_combined_filters_preserve_search_and_category(self):
        with patch.object(app, "load_catalogue", return_value=(self.items, "now")):
            response = self.client.get(
                "/?q=high&category=commercial&deadline=upcoming&min_price=500&max_price=1200",
                headers=self.headers,
            )
        body = response.get_data(as_text=True)
        self.assertIn("Plot high", body)
        self.assertNotIn("Plot low", body)
        self.assertIn('value="high"', body)
        self.assertIn('value="500.0"', body)

    def test_missing_price_does_not_crash_or_match_restricted_range(self):
        with patch.object(app, "load_catalogue", return_value=(self.items, "now")):
            response = self.client.get("/?min_price=200&max_price=900", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Plot missing", response.get_data(as_text=True))

    def test_unverified_location_has_text_but_no_map_navigation(self):
        property_item = item("location", 100)
        property_item["scheme"] = "Vidhyadhar Nagar Sector 1"
        with patch.object(app, "load_catalogue", return_value=([property_item], "now")):
            with patch.object(app, "load_scheme_location", return_value=None):
                response = self.client.get("/property/location", headers=self.headers)
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Vidhyadhar Nagar Sector 1", body)
        self.assertIn("Map navigation is not available yet.", body)
        self.assertNotIn("google.com/maps", body)

    def test_verified_scheme_area_has_safe_map_and_directions_links(self):
        property_item = item("location", 100)
        property_item["scheme"] = "Verified Scheme"
        map_location = {
            "scheme_label": "Verified Scheme",
            "latitude": 26.9124,
            "longitude": 75.7873,
            "precision": "approximate scheme-area",
            "status": "verified",
            "source_url": "https://jda.rajasthan.gov.in/location-plan",
            "evidence_notes": "Verified against a reliable place reference.",
            "coordinate_subject": "scheme area",
            "verification_status": "verified_place_reference",
            "confidence": "high",
        }
        with patch.object(app, "load_catalogue", return_value=([property_item], "now")):
            with patch.object(app, "load_scheme_location", return_value=map_location):
                response = self.client.get("/property/location", headers=self.headers)
        body = response.get_data(as_text=True)
        self.assertIn(
            "Approximate sector/scheme location only — this pin does not identify the exact property, plot boundary, entrance, or legal location.",
            body,
        )
        self.assertIn("https://www.google.com/maps/search/?api=1&query=26.9124,75.7873", body)
        self.assertIn("https://www.google.com/maps/dir/?api=1&destination=26.9124,75.7873", body)
        self.assertIn("https://jda.rajasthan.gov.in/location-plan", body)

    def test_location_registry_accepts_only_verified_approximate_scheme_area(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = os.path.join(directory, "locations.sqlite3")
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE scheme_labels (
                    id INTEGER PRIMARY KEY, label TEXT, normalized_label TEXT
                );
                CREATE TABLE locations (
                    id INTEGER PRIMARY KEY, scheme_label_id INTEGER, latitude REAL,
                    longitude REAL, precision TEXT, status TEXT, source_url TEXT,
                    evidence_notes TEXT, coordinate_subject TEXT,
                    verification_status TEXT, confidence TEXT
                );
                INSERT INTO scheme_labels VALUES
                    (1, 'Verified Scheme', 'verified scheme'),
                    (2, 'Provisional Scheme', 'provisional scheme'),
                    (3, 'Locality Scheme', 'locality scheme'),
                    (4, 'Exact Plot Scheme', 'exact plot scheme');
                INSERT INTO locations VALUES
                    (1, 1, 26.9, 75.7, 'approximate scheme-area', 'verified',
                     'https://example.test/source',
                     'Verified against a reliable map/place reference.',
                     'scheme area', 'verified_place_reference', 'high'),
                    (2, 2, 26.9, 75.7, 'approximate scheme-area', 'provisional',
                     'https://example.test/provisional',
                     'Existing candidate; not independently verified.',
                     'scheme area', 'provisional_existing_pin', 'provisional'),
                    (3, 3, 26.9, 75.7, 'locality_only', 'verified',
                     'https://example.test/locality',
                     'Verified locality reference only.',
                     'locality anchor', 'verified_place_reference', 'high'),
                    (4, 4, 26.9, 75.7, 'exact_plot', 'verified',
                     'https://example.test/exact-plot',
                     'Verified against a reliable map/place reference.',
                     'exact plot', 'verified_place_reference', 'high');
                """
            )
            connection.commit()
            connection.close()
            with patch.object(app, "LOCATION_DB_FILE", app.Path(db_path)):
                accepted = app.load_scheme_location("Verified Scheme")
                self.assertEqual(accepted["scheme_label"], "Verified Scheme")
                self.assertIsNone(app.load_scheme_location("Provisional Scheme"))
                self.assertIsNone(app.load_scheme_location("Locality Scheme"))
                self.assertIsNone(app.load_scheme_location("Exact Plot Scheme"))


if __name__ == "__main__":
    unittest.main()
