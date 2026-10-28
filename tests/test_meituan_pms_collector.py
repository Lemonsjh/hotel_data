"""美团 PMS 请求范围、失败保护及事务边界，不访问网络和真实数据库。"""
import os
import sys
import subprocess
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "meituan_pms"), str(ROOT / "OTA采集服务")]
import collector
import runner
import storage


class MeituanPmsCollectorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 18, 23)
        self.room = {"roomTypeId": "A", "roomTypeName": "大床房", "availableRoomCount": 4}
        self.detail = {"date": "2026-10-27 00:00:00", "availableCount": "4",
                       "occupationCount": "6", "overbookingCount": "0", "adr": 200,
                       "roomRent": 1200, "revPar": 120}
        self.forecast = {"roomTypeName": "大床房", "totalCount": "10", "details": [self.detail]}
        self.session = MagicMock()
        self.session.request.side_effect = [self.response([self.room]), self.response([self.forecast])]

    def response(self, data, status=200, code=10000):
        response = MagicMock(status_code=status)
        response.json.return_value = {"code": code, "data": data}
        return response

    def collect(self, **kwargs):
        return collector.collect("private-cookie", "hotel", "酒店", session=self.session,
                                 clock=kwargs.get("clock", lambda: self.now))

    def test_only_two_requests_and_current_date_range(self):
        rows = self.collect()
        calls = self.session.request.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["GET", "POST"])
        body = calls[1].kwargs["json"]
        self.assertEqual(body["beginDate"], body["endDate"])
        self.assertEqual(body["endDate"], "2026-10-27 00:00:00")
        self.assertEqual(body["beginHour"], "18:00")
        self.assertEqual(body["roomTypes"], [{"id": "A", "roomTypeName": "大床房", "description": None}])
        self.assertTrue(all(call.kwargs["allow_redirects"] is False for call in calls))
        self.assertEqual(rows[0]["room_revenue"], 1200)

    def test_script_entry_loads_with_portable_python(self):
        result = subprocess.run([sys.executable, str(ROOT / "meituan_pms/collector.py"), "--help"],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

    def test_invalid_cookie_and_missing_hotel_stop_before_network(self):
        for cookie, hotel, name in (("", "hotel", "酒店"), ("x\ny", "hotel", "酒店"),
                                    ("cookie", "", "酒店"), ("cookie", "hotel", "")):
            with self.assertRaises(ValueError):
                collector.collect(cookie, hotel, name, session=self.session)
        self.session.request.assert_not_called()

    def test_bad_http_or_business_response_has_no_retry(self):
        for status, code in ((302, 10000), (503, 10000), (200, 401)):
            self.session.request.reset_mock(side_effect=True)
            self.session.request.side_effect = [self.response([], status, code)]
            with self.assertRaises(RuntimeError):
                self.collect()
            self.assertEqual(self.session.request.call_count, 1)

    def test_missing_today_or_cross_midnight_rejected(self):
        self.detail["date"] = "2026-10-28 00:00:00"
        with self.assertRaises(ValueError):
            self.collect()
        self.setUp()
        times = iter([self.now, self.now + timedelta(days=1)])
        with self.assertRaisesRegex(ValueError, "日期边界"):
            self.collect(clock=lambda: next(times))

    def test_dry_run_never_writes_database(self):
        with patch.dict(os.environ, {"MEITUAN_PMS_COOKIE": "cookie", "HOTEL_ID": "hotel",
                                    "MEITUAN_PMS_HOTEL_NAME": "酒店"}), \
             patch.object(collector, "collect", return_value=[{}]), patch.object(collector, "sync_mysql") as sync:
            self.assertEqual(collector.main(["--dry-run"]), 0)
        sync.assert_not_called()

    def test_cookie_env_does_not_fall_back_to_other_platforms(self):
        settings = {"hotel": {"hotel_id": "hotel"}, "meituan_pms": {},
                    "meituan": {"me_cookie": "ota-cookie"}, "pms": {"hotel_name": "other"}}
        with patch.dict(os.environ, {"MEITUAN_PMS_COOKIE": "stale"}):
            env = runner.build_env(settings, "meituan_pms")
        self.assertEqual(env["MEITUAN_PMS_COOKIE"], "")
        self.assertEqual(env["MEITUAN_PMS_HOTEL_NAME"], "")

    def test_two_snapshots_commit_together_and_map_only_meituan_pms(self):
        rows = self.collect()
        connection = MagicMock()
        cur = connection.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[], [], [("大床房", "unified")]]
        storage.sync_mysql(rows, connection)
        self.assertEqual(cur.executemany.call_count, 2)
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()
        sql = " ".join(call.args[0] for call in cur.execute.call_args_list)
        self.assertIn("source_platform='pms_meituan'", sql)
        self.assertTrue(all(call.args[1][0]["room_type_id"] == "unified" for call in cur.executemany.call_args_list))

    def test_other_source_collision_and_insert_failure_roll_back(self):
        rows = self.collect()
        for collision in (True, False):
            connection = MagicMock()
            cur = connection.cursor.return_value.__enter__.return_value
            cur.fetchall.side_effect = [[("A", "PMS（别样红）")]] if collision else [[], [], []]
            if not collision:
                cur.executemany.side_effect = [None, RuntimeError("insert failed")]
            with self.assertRaises((ValueError, RuntimeError)):
                storage.sync_mysql(rows, connection)
            connection.rollback.assert_called_once()
            connection.commit.assert_not_called()
            if collision:
                self.assertFalse(any("DELETE" in call.args[0] for call in cur.execute.call_args_list))
