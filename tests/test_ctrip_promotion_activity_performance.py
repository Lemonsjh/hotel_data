from __future__ import annotations

import copy
import importlib.util
import json
import sqlite3
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
CTRIP_DIR = ROOT / "携程OTA数据采集代码"


def load_module():
    sys.path.insert(0, str(CTRIP_DIR))
    path = CTRIP_DIR / "ctrip_promotion_activity_performance_data.py"
    spec = importlib.util.spec_from_file_location("ctrip_promotion_performance_test_target", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def payload():
    return {
        "overview": {
            "resStatus": {"rcode": 200}, "ResponseStatus": {"Ack": "Success", "Errors": []},
            "promoRoomNights": 120, "totalRoomNights": 140, "competitorsAvgPromoRoomNights": 80.5,
        },
        "details": {"resStatus": {"rcode": 200}, "details": [
            {"promoSet": "天天特价", "promoType": "基础促销", "promoRoomNights": 110,
             "promoGmv": 12000, "promoAvgDiscount": 0.15, "promoOrderCount": 85},
            {"promoSet": "商旅专享", "promoType": "场景促销", "promoRoomNights": 0, "promoGmv": 0},
        ]},
    }


class CtripPromotionActivityPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def rows(self, data=None):
        return self.module.rows_from_payload(
            payload() if data is None else data, "hotel-1", datetime(2026, 10, 24, 12),
            date(2026, 9, 24), date(2026, 10, 23),
        )

    def test_uses_the_completed_30_day_window(self):
        for today, start, end in (
            (date(2026, 10, 24), date(2026, 9, 24), date(2026, 10, 23)),
            (date(2026, 1, 1), date(2025, 12, 2), date(2025, 12, 31)),
            (date(2024, 3, 1), date(2024, 1, 31), date(2024, 2, 29)),
        ):
            self.assertEqual(self.module.rolling_30_day_range(today), (start, end))

    def test_builds_rows_from_new_page_apis_and_leaves_missing_fields_null(self):
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "hotel-1")
        self.assertEqual(rows[0][3:5], [date(2026, 9, 24), date(2026, 10, 23)])
        self.assertEqual(rows[0][6:12], [None, 120, 140, 80.5, None, None])
        self.assertEqual(rows[0][12:], [None, "天天特价", 110, None, 12000, None, None])
        self.assertTrue(all(len(row) == len(self.module.HEADERS) for row in rows))

    def test_zero_values_are_preserved_without_fabricating_discount_amount(self):
        row = self.rows()[1]
        self.assertEqual(row[14], 0)
        self.assertEqual(row[16], 0)
        self.assertIsNone(row[18])

    def test_optional_missing_numbers_are_null(self):
        data = payload()
        data["overview"] = {"resStatus": {"rcode": 200}}
        data["details"]["details"] = [{"promoSet": "测试促销"}]
        row = self.rows(data)[0]
        self.assertEqual(row[6:13], [None] * 7)
        self.assertEqual(row[14:], [None] * 5)

    def test_empty_details_are_valid_but_not_missing_or_malformed_details(self):
        data = payload()
        data["details"]["details"] = []
        self.assertEqual(self.rows(data), [])
        for invalid in (None, {}, ""):
            data["details"]["details"] = invalid
            with self.assertRaises(RuntimeError):
                self.rows(data)

    def test_invalid_names_and_duplicates_reject_the_entire_batch(self):
        for invalid in (None, {}, {"promoSet": " "}, {"promoSet": "x" * 256}, {"promoSet": 1}):
            data = payload()
            data["details"]["details"].append(invalid)
            with self.assertRaises(RuntimeError):
                self.rows(data)
        data = payload()
        data["details"]["details"] = [{"promoSet": "Test"}, {"promoSet": " test "}]
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            self.rows(data)

    def test_business_failure_in_either_response_is_rejected(self):
        for name in ("details", "overview"):
            for code in (0, 401, 500, None, True):
                data = payload()
                data[name]["resStatus"]["rcode"] = code
                with self.assertRaises(RuntimeError):
                    self.rows(data)

    def test_service_errors_and_legacy_payload_are_rejected(self):
        data = payload()
        data["overview"]["ResponseStatus"]["Ack"] = "Failure"
        with self.assertRaises(RuntimeError):
            self.rows(data)
        with self.assertRaises(RuntimeError):
            self.rows({"rcode": 0, "data": {"promotionDetailBo": []}})
        data = payload()
        data["details"]["ResponseStatus"] = {"Ack": "Success", "Errors": [{"ErrorCode": "123"}]}
        with self.assertRaises(RuntimeError):
            self.rows(data)

    def test_http_or_non_json_response_does_not_become_an_empty_success(self):
        for result in ({"status": 401, "text": "{}"}, {"status": 200, "text": "<html>login</html>"}, None):
            with self.assertRaises(RuntimeError):
                self.module.decode_response(result)

    def test_nullable_id_conversion_and_name_key_allow_distinct_rows(self):
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE TABLE effect(hotel_id TEXT, platform_scope TEXT, campaign_id INTEGER NULL, "
                       "campaign_name TEXT NOT NULL, PRIMARY KEY(hotel_id,platform_scope,campaign_name))")
            db.executemany("INSERT INTO effect VALUES(?,?,?,?)",
                           [(r[0], r[2], r[12], r[13]) for r in self.rows()])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM effect WHERE campaign_id IS NULL").fetchone()[0], 2)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO effect VALUES('hotel-1','ctrip',NULL,'天天特价')")
        finally:
            db.close()

    def test_nonempty_sync_uses_existing_transactional_scoped_writer(self):
        rows = self.rows()
        with patch.object(self.module, "sync_table") as writer:
            self.module.sync_rows(rows)
        writer.assert_called_once_with(self.module.TABLE_NAME, self.module.HEADERS, rows, allow_empty_replace=True)

    def test_empty_sync_never_calls_full_table_replacement(self):
        with patch.object(self.module, "sync_table") as writer:
            self.module.sync_rows([])
        writer.assert_not_called()

    def browser_setup(self, missing_overview=False):
        page = MagicMock()
        page.url = self.module.PROMOTION_PAGE_URL
        browser = MagicMock()
        browser.new_context.return_value.new_page.return_value = page
        runtime = MagicMock()
        runtime.chromium.launch.return_value = browser
        callbacks = {}
        page.on.side_effect = lambda event, callback: callbacks.update({event: callback})

        def emit(start, end):
            for name, path in (("overview", self.module.OVERVIEW_PATH), ("details", self.module.DETAIL_PATH)):
                if missing_overview and name == "overview":
                    continue
                body = copy.deepcopy(payload()[name])
                if start != "2026-09-24" and name == "overview":
                    body["promoRoomNights"] = 9999
                callbacks["response"](SimpleNamespace(
                    url="https://ebooking.ctrip.com" + path + "?_nonce=sample", status=200,
                    request=SimpleNamespace(post_data_json={"startDate": start, "endDate": end}),
                    text=lambda body=body: json.dumps(body),
                ))

        buttons = {"促销效果": MagicMock(), "过去30天": MagicMock()}
        buttons["促销效果"].click.side_effect = lambda **kw: emit("2026-10-17", "2026-10-23")
        buttons["过去30天"].click.side_effect = lambda **kw: emit("2026-09-24", "2026-10-23")
        page.get_by_text.side_effect = lambda text, **kw: buttons[text]
        return runtime, browser, page

    def test_captures_current_page_responses_matching_only_requested_dates(self):
        runtime, browser, page = self.browser_setup()
        with patch.object(self.module, "sync_playwright") as start, patch.object(
            self.module, "rolling_30_day_range", return_value=(date(2026, 9, 24), date(2026, 10, 23)),
        ):
            start.return_value.__enter__.return_value = runtime
            data = self.module.fetch_payload(date(2026, 9, 24), date(2026, 10, 23), "session=fake")
        self.assertEqual(data["overview"]["promoRoomNights"], 120)
        page.evaluate.assert_not_called()
        browser.close.assert_called_once()

    def test_missing_response_and_login_redirect_fail_and_close_browser(self):
        for redirected in (False, True):
            runtime, browser, page = self.browser_setup(missing_overview=True)
            if redirected:
                page.url = "https://ebooking.ctrip.com/login/index"
            with patch.object(self.module, "sync_playwright") as start, patch.object(
                self.module, "rolling_30_day_range", return_value=(date(2026, 9, 24), date(2026, 10, 23)),
            ), patch.object(self.module.time, "monotonic", side_effect=[0, 31]):
                start.return_value.__enter__.return_value = runtime
                with self.assertRaises(RuntimeError):
                    self.module.fetch_payload(date(2026, 9, 24), date(2026, 10, 23), "session=fake")
            browser.close.assert_called_once()

    def test_schema_and_migrations_allow_null_id_with_name_primary_key(self):
        paths = [ROOT / "OTA采集服务/database/create_all_tables.sql",
                 ROOT / "OTA采集服务/database/migrations/ctrip_ota_promotion_activity_performance_migration.sql"]
        for path in paths:
            sql = path.read_text(encoding="utf-8")
            table = sql.split("CREATE TABLE IF NOT EXISTS `ctrip_ota_promotion_activity_performance`", 1)[1].split("ENGINE=", 1)[0]
            self.assertIn("`campaign_id` bigint unsigned DEFAULT NULL", table)
            self.assertIn("PRIMARY KEY (`hotel_id`,`platform_scope`,`campaign_name`)", table)
        migration = (ROOT / "OTA采集服务/database/migrations/ctrip_promotion_effect_name_key.sql").read_text()
        self.assertIn("ADD PRIMARY KEY (hotel_id, platform_scope, campaign_name)", migration)
        self.assertNotIn("DELETE FROM", migration)


if __name__ == "__main__":
    unittest.main()
