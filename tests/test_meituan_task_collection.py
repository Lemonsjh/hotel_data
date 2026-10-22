from __future__ import annotations

import importlib.util
import sys
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
MEITUAN_DIR = ROOT / "美团OTA数据采集代码"


def load_module(filename: str, module_name: str):
    sys.path.insert(0, str(MEITUAN_DIR))
    spec = importlib.util.spec_from_file_location(module_name, MEITUAN_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class MeituanTaskCollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scan = load_module("meituan_scan_order_data.py", "meituan_scan_order_test_target")
        cls.video = load_module("meituan_video_upload_status_data.py", "meituan_video_upload_test_target")

    def test_scan_order_accepts_successful_empty_data(self):
        response = _Response({"status": 0, "data": None})
        with patch.object(self.scan, "MEITUAN_EB_COOKIE", "test-cookie"), patch.object(
            self.scan.requests, "get", return_value=response
        ):
            data = self.scan.request_page(date(2026, 8, 1), date(2026, 8, 2), 1)
        self.assertEqual(data, {})

    def test_scan_order_failure_includes_platform_message(self):
        response = _Response({"status": 303, "msg": "登录状态失效", "data": None})
        with patch.object(self.scan, "MEITUAN_EB_COOKIE", "test-cookie"), patch.object(
            self.scan.requests, "get", return_value=response
        ):
            with self.assertRaisesRegex(RuntimeError, "status=303; message=登录状态失效"):
                self.scan.request_page(date(2026, 8, 1), date(2026, 8, 2), 1)

    def test_video_rows_are_read_from_task_api_payload(self):
        payload = {
            "status": 0,
            "data": {
                "hasHotelOfficial": 1,
                "hasHotelOfficialPreview": 0,
                "hasRoomVideoOnlineRealRoomNum": 7,
                "onlineRealRoomNum": 10,
            },
        }
        self.assertEqual(
            self.video.video_rows_from_payload(payload),
            [
                ("hotel_official_video", 1, 1),
                ("hotel_official_preview_video", 0, 1),
                ("room_type_video", 7, 10),
            ],
        )

    def test_video_task_response_matches_only_get_endpoint(self):
        response = SimpleNamespace(
            url="https://tdc.meituan.com/gw/tdc/hubble/eb/hotel/video/poi/video/task?poiId=1879794992&mtgsig=signed",
            request=SimpleNamespace(method="GET"),
        )
        self.assertTrue(self.video.is_video_task_response(response))
        response.request.method = "POST"
        self.assertFalse(self.video.is_video_task_response(response))

    def test_video_task_payload_rejects_failed_or_missing_data(self):
        with self.assertRaises(RuntimeError):
            self.video.video_rows_from_payload({"status": 1, "data": {}})
        with self.assertRaisesRegex(RuntimeError, "hasHotelOfficialPreview"):
            self.video.video_rows_from_payload(
                {
                    "status": 0,
                    "data": {
                        "hasHotelOfficial": 1,
                        "hasRoomVideoOnlineRealRoomNum": 7,
                        "onlineRealRoomNum": 10,
                    },
                }
            )

    def test_video_uses_current_eb_session_for_tdc_domain(self):
        with patch.object(self.video, "MEITUAN_ME_COOKIE", "session=me-account; mebsid=me-token"), patch.object(
            self.video, "MEITUAN_EB_COOKIE", "session=eb-account; ebbsid=eb-token"
        ):
            cookies = self.video.video_browser_cookies()
        domains = {url: {item["name"]: item["value"] for item in cookies if item["url"] == url}
                   for url in ("https://me.meituan.com/", "https://eb.meituan.com/", "https://tdc.meituan.com/")}
        self.assertEqual(domains["https://tdc.meituan.com/"], domains["https://eb.meituan.com/"])
        self.assertEqual(domains["https://tdc.meituan.com/"]["session"], "eb-account")
        self.assertEqual(domains["https://me.meituan.com/"]["session"], "me-account")

    def test_video_missing_eb_cookie_does_not_use_old_profile_session(self):
        with patch.object(self.video, "MEITUAN_ME_COOKIE", "mebsid=me-token"), patch.object(
            self.video, "MEITUAN_EB_COOKIE", ""
        ), patch.object(self.video, "sync_playwright") as playwright:
            with self.assertRaisesRegex(RuntimeError, "eb_cookie"):
                self.video.fetch_video_counts()
        playwright.assert_not_called()

    def test_video_reports_tdc_login_failure_without_exposing_response(self):
        with self.assertRaisesRegex(RuntimeError, r"tdc\.meituan\.com.*status=303") as caught:
            self.video.video_rows_from_payload({"status": 303, "message": "secret-session-value", "data": None})
        self.assertNotIn("secret-session-value", str(caught.exception))

    def test_video_save_updates_all_rows_with_one_snapshot_time(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        rows = [("hotel_official_video", 1, 1), ("room_type_video", 7, 10)]
        with patch.dict(sys.modules, {"pymysql": SimpleNamespace(connect=lambda **_kwargs: connection)}):
            self.video.save_video_counts("hotel-1", rows)
        _query, values = cursor.executemany.call_args.args
        self.assertEqual([value[-1] for value in values][0], [value[-1] for value in values][1])
        self.assertIn("snapshot_time", cursor.executemany.call_args.args[0])


class ReviewRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target = load_module("meituan_review_detail_data.py", "meituan_review_refresh_test_target")

    @staticmethod
    def payload(page, total=200, **changes):
        items = [{"id": f"old-{page}-{i}", "comment": "更新后的内容", "bizReply": "更新后的回复",
                  "replyId": "reply-1", "replyTime": 1792627200000, "readStatus": 1}
                 for i in range(10)]
        return {"code": 10000, "data": {"total": total, "commentList": items, **changes}}

    def collect(self, responses, max_pages=10):
        with patch.object(self.target, "MeituanReviewDetailClient") as factory, \
             patch.object(self.target.time, "sleep"), patch("builtins.print"):
            client = factory.return_value
            client.fetch_page.side_effect = responses
            result = self.target.collect_online("https://example.test", 1, "美团", 10, max_pages)
        return result, client

    def test_refresh_reads_all_ten_pages_and_updates_old_comment_fields(self):
        (rows, counts), client = self.collect([self.payload(page) for page in range(1, 11)])
        self.assertEqual(client.fetch_page.call_count, 10)
        self.assertEqual([call.args for call in client.fetch_page.call_args_list], [(page, 10) for page in range(1, 11)])
        self.assertEqual(len(rows), 100)
        tenth = next(row for row in rows if row[4] == "old-10-0")
        values = dict(zip(self.target.HEADERS, tenth))
        self.assertEqual(values["review_content"], "更新后的内容")
        self.assertEqual(values["merchant_reply_content"], "更新后的回复")
        self.assertTrue(values["is_replied"])
        self.assertIsNotNone(values["merchant_reply_time"])
        self.assertEqual(values["read_status"], 1)
        self.assertEqual(counts["total_review_count"], 200)
        client.close.assert_called_once()

    def test_short_history_stops_at_reported_total(self):
        (rows, _), client = self.collect([self.payload(1, total=20), self.payload(2, total=20)])
        self.assertEqual(len(rows), 20)
        self.assertEqual(client.fetch_page.call_count, 2)

    def test_empty_or_repeated_api_page_still_stops_safely(self):
        for second in (self.payload(2, commentList=[]), self.payload(1)):
            with self.subTest(second=second):
                (rows, _), client = self.collect([self.payload(1), second])
                self.assertEqual(len(rows), 10)
                self.assertEqual(client.fetch_page.call_count, 2)

    def test_main_with_existing_reviews_uses_ten_pages_for_both_channels(self):
        existing = {"美团": {"old-1-0"}, "大众点评": {"old-1-0"}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["review-detail"]))
            stack.enter_context(patch.object(self.target, "MEITUAN_ME_COOKIE", "fake-cookie"))
            stack.enter_context(patch.object(self.target, "load_existing_review_ids", return_value=existing))
            collect = stack.enter_context(patch.object(self.target, "collect_online", return_value=([], {})))
            stack.enter_context(patch.object(self.target, "save_outputs"))
            stack.enter_context(patch.object(self.target, "upsert_mysql"))
            stack.enter_context(patch.object(self.target, "sync_overview_counts"))
            stack.enter_context(patch("builtins.print"))
            self.target.main()
        self.assertEqual(collect.call_count, 2)
        self.assertEqual([call.args[-2:] for call in collect.call_args_list], [(10, 10), (10, 10)])

    def test_first_collection_retains_existing_page_limits(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["review-detail"]))
            stack.enter_context(patch.object(self.target, "MEITUAN_ME_COOKIE", "fake-cookie"))
            stack.enter_context(patch.object(self.target, "load_existing_review_ids", return_value={}))
            collect = stack.enter_context(patch.object(self.target, "collect_online", return_value=([], {})))
            stack.enter_context(patch.object(self.target, "save_outputs"))
            stack.enter_context(patch.object(self.target, "upsert_mysql"))
            stack.enter_context(patch.object(self.target, "sync_overview_counts"))
            stack.enter_context(patch("builtins.print"))
            self.target.main()
        self.assertEqual([call.args[-1] for call in collect.call_args_list], [30, 10])

    def test_upsert_updates_reply_fields_without_deleting_older_comments(self):
        rows = self.target.normalize_rows(self.payload(10), "美团")
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        with patch.object(self.target.pymysql, "connect", return_value=connection), patch("builtins.print"):
            self.target.upsert_mysql(self.target.HEADERS, rows)
        query, values = cursor.executemany.call_args.args
        self.assertIn("ON DUPLICATE KEY UPDATE", query)
        for field in ("review_content", "merchant_reply_content", "merchant_reply_time", "is_replied", "read_status"):
            self.assertIn(f"`{field}`=VALUES(`{field}`)", query)
        self.assertNotIn("DELETE", query)
        cursor.execute.assert_not_called()
        self.assertEqual(len(values), 10)
        connection.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
