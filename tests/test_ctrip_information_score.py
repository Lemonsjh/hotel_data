from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
CTRIP_DIR = ROOT / "携程OTA数据采集代码"


def load_module():
    sys.path.insert(0, str(CTRIP_DIR))
    path = CTRIP_DIR / "ctrip_promotion_status_data.py"
    spec = importlib.util.spec_from_file_location("ctrip_information_score_test_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CtripInformationScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_reads_current_score_from_api_payload(self):
        self.assertEqual(self.module.information_score_from_payload({
            "success": True, "scoreInfo": {"currentScore": 96, "fullScore": 100, "scoreText": "96%"},
        }), 96.0)

    def test_rejects_missing_or_failed_score(self):
        for payload in ({"success": False, "scoreInfo": {"currentScore": 96}},
                        {"success": True, "scoreInfo": {}},
                        {"success": True, "scoreInfo": {"currentScore": "not-a-score"}}):
                with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                    self.module.information_score_from_payload(payload)

    def test_filters_non_deletable_listing_short_tags(self):
        payload = {
            "resStatus": {"rcode": 200},
            "shortTags": [
                {"tagName": "洗衣房", "canDelete": False},
                {"tagName": "亲子主题房", "canDelete": True},
                {"tagName": "免费停车场", "canDelete": False},
            ],
        }
        self.assertEqual(self.module.short_tag_names_from_payload(payload), "洗衣房，免费停车场")

    def test_reads_current_listing_recommendation_words(self):
        payload = {
            "resStatus": {"rcode": 200},
            "ugcCommentClause": {"info": {"clause": "服务热情，设施齐全，干净卫生"}},
        }
        self.assertEqual(self.module.recommendation_words_from_payload(payload), "服务热情，设施齐全，干净卫生")

    def test_detects_quick_check_inn_application_page_as_not_joined(self):
        page = MagicMock()
        page.locator.return_value.inner_text.return_value = "请选择押金系数 我已阅读并同意《闪住加盟邀约》 立即在线加盟"
        with patch.object(self.module, "ensure_logged_in"):
            self.assertEqual(self.module.quick_check_inn_status(page), 0)
        page.goto.assert_called_once_with(self.module.QUICK_CHECK_INN_URL, wait_until="domcontentloaded", timeout=60_000)

    def test_detects_loaded_quick_check_inn_page_without_application_as_joined(self):
        page = MagicMock()
        page.url = self.module.QUICK_CHECK_INN_URL
        page.locator.return_value.inner_text.return_value = "闪住服务已开通，订单将自动结算"
        with patch.object(self.module, "ensure_logged_in"):
            self.assertEqual(self.module.quick_check_inn_status(page), 1)

    def test_uses_page_request_and_expands_information_menu(self):
        page = MagicMock()
        home = MagicMock()
        home.is_visible.return_value = False
        menu = MagicMock()
        page.get_by_text.side_effect = lambda text, exact: SimpleNamespace(first=home if text == "信息首页" else menu)
        response = SimpleNamespace(url="https://ebooking.ctrip.com/restapi/soa2/23942/getHotelInfoScoreItems")
        page.expect_response.return_value.__enter__.return_value.value.json.return_value = {
            "success": True, "scoreInfo": {"currentScore": 96},
        }
        with patch.object(self.module, "ensure_logged_in"), patch.object(self.module, "dismiss_overlays"):
            self.assertEqual(self.module.information_completeness_score(page), 96.0)
        page.get_by_text.assert_any_call("信息维护", exact=True)
        page.get_by_text.assert_any_call("信息首页", exact=True)
        self.assertTrue(page.expect_response.call_args.args[0](response))
        menu.click.assert_called_once()
        home.click.assert_called_once()

    def test_promotion_rows_exclude_listing_pass(self):
        results = {key: (0, None) for key in (
            "points_alliance", "preferred_club", "business_travel", "travel_photo", "listing_short_tags", "listing_recommendation_words", "quick_check_inn",
        )}
        results["listing_short_tags"] = ("洗衣房，免费停车场", None)
        results["listing_recommendation_words"] = ("服务热情，设施齐全", None)
        results.update(hourly_room=((0, 0), None), information=(96, None))
        rows = self.module.status_rows("hotel-1", datetime(2026, 9, 16), results)
        self.assertEqual(len(rows), 9)
        self.assertNotIn("listing_pass", [row[3] for row in rows])
        self.assertNotIn("homepage_video", [row[3] for row in rows])
        short_tags = next(row for row in rows if row[3] == "listing_short_tags")
        self.assertEqual(short_tags[7], "洗衣房，免费停车场")
        recommendation = next(row for row in rows if row[3] == "listing_recommendation_words")
        self.assertEqual(recommendation[7], "服务热情，设施齐全")
        quick_check_inn = next(row for row in rows if row[3] == "quick_check_inn")
        self.assertEqual(quick_check_inn[6], "not_joined")

if __name__ == "__main__":
    unittest.main()
