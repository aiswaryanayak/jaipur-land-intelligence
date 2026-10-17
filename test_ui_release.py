import base64
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


if __name__ == "__main__":
    unittest.main()
