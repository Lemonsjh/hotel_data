from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MEITUAN_DIR = ROOT / "美团OTA数据采集代码"


def load_module():
    sys.path.insert(0, str(MEITUAN_DIR))
    path = MEITUAN_DIR / "bussiness_data.py"
    spec = importlib.util.spec_from_file_location("meituan_business_hourly_test_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MeituanBusinessHourlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.module.HOTEL_ID = "test-hotel"

    def test_build_hourly_rows_keep_ten_realtime_metrics_with_peer_data(self):
        metrics = {
            "DAY_ROOM_LOWEST_PRICE_AVG": {"value": "197.00", "unit": "元", "rank": "13/20", "peer_avg": "218.1"},
            "EXPOSURE_UV": {"value": "530", "name": "曝光人数", "unit": "次"},
            "INTENTION_UV": {"value": "65", "unit": "人"},
            "PAY_ORDER_CNT_UV": {"value": "1.54", "unit": "%"},
            "PAY_ORDER_CNT": {"value": "1", "unit": "单"},
            "PAY_ROOMNIGHT": {"value": "1", "unit": "间夜"},
            "PAY_ADR": {"value": "88.00", "unit": "元"},
            "PAY_AMT": {"value": "88.00", "unit": "元"},
            "CONSUME_ROOMNIGHT_SPLIT_EX_7DAYS_REFUND": {"value": "2", "unit": "间夜"},
            "NOT_AVAILABLE_REAL_ROOM_RATE": {"value": "0.00", "unit": "%"},
            "FLOW_EXPOSURE_UV": {"value": "999", "unit": "人"},
        }
        captured_at = datetime(2026, 8, 18, 10, 56, 32)

        rows = self.module.build_hourly_metric_rows({"score_hotel_name": "测试酒店", "metrics": metrics}, captured_at)
        values = {row[self.module.HOURLY_HEADERS.index("metric_code")]: dict(zip(self.module.HOURLY_HEADERS, row)) for row in rows}

        self.assertEqual(len(rows), 10)
        self.assertEqual(values["PAY_AMT"]["hotel_id"], "test-hotel")
        self.assertEqual(values["PAY_AMT"]["business_date"].isoformat(), "2026-08-18")
        self.assertEqual(values["PAY_AMT"]["snapshot_hour"], datetime(2026, 8, 18, 10))
        self.assertEqual(values["DAY_ROOM_LOWEST_PRICE_AVG"]["metric_value"], 197)
        self.assertEqual(values["DAY_ROOM_LOWEST_PRICE_AVG"]["competitor_rank"], "13/20")
        self.assertEqual(values["DAY_ROOM_LOWEST_PRICE_AVG"]["peer_average"], "218.1")
        self.assertEqual(values["EXPOSURE_UV"]["metric_value"], 530)
        self.assertEqual(values["EXPOSURE_UV"]["metric_name"], "曝光人数")
        self.assertNotIn("EXPOSE_PV_CNT", values)
        self.assertEqual(values["PAY_ORDER_CNT_UV"]["metric_value"], 1.54)
        self.assertEqual(values["NOT_AVAILABLE_REAL_ROOM_RATE"]["metric_value"], 0)
        self.assertNotIn("FLOW_EXPOSURE_UV", values)

    def test_percent_metric_value_keeps_the_page_percentage(self):
        self.assertEqual(self.module.excel_metric_value("1.54", "%"), 1.54)
        self.assertEqual(self.module.excel_metric_value("1.54%"), 1.54)

    def dashboard_response(self, value):
        return {"status": 0, "data": {"cards": [{"id": "PAY_ORDER_CNT", "value": value}]}}

    def test_dashboard_recovers_on_first_or_second_retry(self):
        for date_range, empty_values in [(0, [None]), (1, ["", "--"])]:
            with self.subTest(date_range=date_range):
                client = self.module.MeituanDashboardClient("")
                responses = [self.dashboard_response(v) for v in [*empty_values, "10"]]
                with patch.object(client, "_get_api", side_effect=responses) as get_api, patch.object(self.module.time, "sleep") as sleep:
                    result = client.get_dashboard(date_range=date_range)
                self.assertEqual(result["metrics"]["PAY_ORDER_CNT"]["value"], "10")
                self.assertEqual(get_api.call_count, len(responses))
                self.assertEqual([c.args[0] for c in sleep.call_args_list], [5, 10][:len(empty_values)])
                self.assertTrue(all(c.args[1]["dateRange"] == date_range for c in get_api.call_args_list))

    def test_missing_or_empty_payment_stops_after_two_retries(self):
        responses = [{"status": 0, "data": {"cards": []}}, self.dashboard_response(None), self.dashboard_response("-")]
        client = self.module.MeituanDashboardClient("")
        with patch.object(client, "_get_api", side_effect=responses) as get_api, patch.object(self.module.time, "sleep") as sleep, patch.object(client, "get_flow_conversion") as flow:
            with self.assertRaisesRegex(RuntimeError, "补采2次"):
                client.get_report_data(date_range=0)
        self.assertEqual(get_api.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        flow.assert_not_called()

    def test_zero_and_thousands_are_valid_without_retry(self):
        for value, expected in [("0", 0), (0, 0), ("1,234", 1234)]:
            client = self.module.MeituanDashboardClient("")
            with patch.object(client, "_get_api", return_value=self.dashboard_response(value)) as get_api, patch.object(self.module.time, "sleep") as sleep:
                result = client.get_dashboard()
            self.assertEqual(self.module.hourly_metric_value(result["metrics"]["PAY_ORDER_CNT"]), expected)
            get_api.assert_called_once()
            sleep.assert_not_called()

    def test_request_failure_is_not_retried(self):
        client = self.module.MeituanDashboardClient("")
        with patch.object(client, "_get_api", side_effect=RuntimeError("request failed")) as get_api, patch.object(self.module.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "request failed"):
                client.get_dashboard()
        get_api.assert_called_once()
        sleep.assert_not_called()

    def test_daily_rows_include_exposure_people_for_today_and_yesterday(self):
        client = self.module.MeituanDashboardClient("")
        data = client._parse_dashboard({"data": {"cards": [{"id": "EXPOSURE_UV", "title": "曝光人数", "value": "776"}]}})
        for period in ("today_realtime", "yesterday"):
            rows = self.module.build_metric_rows(data, period, 0, datetime(2026, 11, 4, 15, 23))
            self.assertEqual(rows[0][3:6], ["EXPOSURE_UV", "曝光人数", 776])


if __name__ == "__main__":
    unittest.main()
