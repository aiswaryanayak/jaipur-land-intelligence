import unittest

from collect_rhb_land_records import parse_row, relevant
from collect_riico_staging import IST


class RHBCollectorTests(unittest.TestCase):
    def test_filters_non_land_work_tenders(self):
        self.assertTrue(relevant("NIT regarding e-Auction"))
        self.assertFalse(relevant("Construction and maintenance of roads"))

    def test_parses_historical_eauction_with_explicit_missing_fields(self):
        row = parse_row(
            ["", "NIT regarding e-Auction", "1184", "18/6/2015", "2/7/2015", "17/7/2015", "21/7/2015"],
            __import__("datetime").datetime(2026, 10, 18, tzinfo=IST),
        )
        self.assertEqual(row["original_identifier"], "1184")
        self.assertEqual(row["current_status"], "historical")
        self.assertEqual(row["property_or_plot_number"], "")


if __name__ == "__main__":
    unittest.main()
