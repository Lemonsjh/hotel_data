"""别样红 JY01 首次及后续固定 30 天；RS01 历史策略保持独立。"""
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "别样红PMS数据采集代码/PMS登录/scripts"))
import pms_history


class ByhJy01WindowTests(unittest.TestCase):
    def test_fixed_thirty_day_plan_needs_no_history_database_probe(self):
        for today, start, end in ((date(2026, 10, 27), "2026-09-27", "2026-10-26"),
                                  (date(2027, 1, 1), "2026-12-02", "2026-12-31"),
                                  (date(2024, 3, 1), "2024-01-31", "2024-02-29")):
            with self.subTest(today=today), patch.object(pms_history, "date") as clock, \
                 patch.object(pms_history, "load_settings") as load, patch.object(pms_history.pymysql, "connect") as connect:
                clock.today.return_value = today
                plan = pms_history.query_plan("jy01_hotel_statistics_daily", "JY01")
                self.assertEqual(plan, (False, [("最近30天", start, end)]))
                load.assert_not_called()
                connect.assert_not_called()

    def test_rs01_still_uses_six_month_backfill_or_current_month_refresh(self):
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        with patch.object(pms_history, "load_settings", return_value={"hotel": {"hotel_id": "test"}}), \
             patch.object(pms_history, "mysql_config", return_value={}), \
             patch.object(pms_history.pymysql, "connect", return_value=connection):
            cursor.fetchone.return_value = {"month_count": 0}
            backfill, windows = pms_history.query_plan("rs01_room_revenue_daily", "RS01")
            self.assertTrue(backfill)
            self.assertEqual(len(windows), 6)
            cursor.fetchone.return_value = {"month_count": 6}
            backfill, windows = pms_history.query_plan("rs01_room_revenue_daily", "RS01")
            self.assertFalse(backfill)
            self.assertEqual(windows, [pms_history.incremental_window("RS01")])
