import unittest
from datetime import datetime

from collect_jda_institutional_allotments import IST, parse_row


class JDAInstitutionalAllotmentTests(unittest.TestCase):
    def test_preserves_allotment_fields_and_historical_status(self):
        row = parse_row(
            ["1", "ZONE 01", "Government institution", "Example Department",
             "20 - Jhalana Dungar", "2000 SQ.METER", "20-Jun-2023",
             "15000.00 (Rs)", "FOR OFFICE"],
            datetime(2026, 10, 18, tzinfo=IST),
        )
        self.assertEqual(row["current_status"], "historical")
        self.assertEqual(row["property_or_plot_number"], "20 - Jhalana Dungar")
        self.assertEqual(row["reserve_price_or_rate"], "15000.00 (Rs)")


if __name__ == "__main__":
    unittest.main()
