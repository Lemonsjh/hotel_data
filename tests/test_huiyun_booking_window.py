from datetime import date
import unittest

from huiyun_pms.booking_window import collection_window, retention_cutoff


class HuiyunBookingWindowTests(unittest.TestCase):
    def test_initial_window_and_later_window_include_today(self):
        day = date(2026, 11, 5)
        self.assertEqual(collection_window(day, False), (date(2026, 10, 7), day))
        self.assertEqual(collection_window(day, True), (day, day))

    def test_retention_keeps_ninety_calendar_days_including_today(self):
        self.assertEqual(retention_cutoff(date(2026, 11, 5)), date(2026, 8, 8))
        self.assertEqual(retention_cutoff(date(2024, 3, 1)), date(2023, 12, 3))


if __name__ == '__main__':
    unittest.main()
