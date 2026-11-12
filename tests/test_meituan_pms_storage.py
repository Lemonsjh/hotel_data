import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meituan_pms import storage


class MeituanPmsStorageTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 12, 12, 15, 43)
        self.row = {"hotel_id": "puyue", "hotel_name": "酒店", "source_platform": storage.SOURCE,
                    "snapshot_time": self.now, "stay_date": date(2026, 11, 12), "snapshot_hour": datetime(2026, 11, 12, 12),
                    "room_type_name": "大床房", "pms_room_type_id": "A", "total_rooms": 10, "available_rooms": 4,
                    "occupied_rooms": 6, "overbooking_rooms": 0, "room_revenue": 500, "adr": 83.33, "revpar": 50}

    def run_sync(self, rows=None):
        connection = MagicMock()
        cur = connection.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        storage.sync_mysql(rows or [self.row], connection)
        return [call.args[0] for call in cur.execute.call_args_list]

    def test_forecast_is_replaced_for_the_whole_hotel(self):
        sqls = self.run_sync()
        self.assertIn(f"DELETE FROM `{storage.FORECAST}` WHERE hotel_id=%s", sqls)
        self.assertFalse(any("source_platform" in sql and storage.FORECAST in sql for sql in sqls))

    def test_hourly_only_replaces_same_hour_of_same_source(self):
        sqls = self.run_sync()
        self.assertTrue(any(f"DELETE FROM `{storage.HOURLY}`" in sql and "source_platform=%s" in sql
                            and "`snapshot_hour`=%s" in sql for sql in sqls))

    def test_other_pms_hourly_snapshot_still_rejects_takeover(self):
        connection = MagicMock()
        cur = connection.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [("A", "PMS（别样红）")]
        with self.assertRaisesRegex(ValueError, "其他 PMS"):
            storage.sync_mysql([self.row], connection)
        connection.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
