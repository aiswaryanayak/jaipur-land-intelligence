import unittest
from datetime import datetime

from collect_rhb_allotment_draws import IST, parse_date, parse_row


class RHBAllotmentDrawTests(unittest.TestCase):
    def test_parses_supported_dates(self):
        self.assertEqual(parse_date("30-03-2026").year, 2026)
        self.assertEqual(parse_date("14.10.2026").month, 10)

    def test_stages_draw_as_historical_not_available(self):
        row = parse_row(
            ["", "Property Allotment Lottery, Mansarovar, Jaipur", "30-03-2026", ""],
            "/content/dam/rhb/example.pdf",
            datetime(2026, 10, 18, tzinfo=IST),
        )
        self.assertEqual(row["source_type"], "scheme allotment notice")
        self.assertEqual(row["current_status"], "historical")
        self.assertEqual(row["property_or_plot_number"], "")


if __name__ == "__main__":
    unittest.main()
