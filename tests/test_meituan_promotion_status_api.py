"""促销状态接口、身份与页面降级保护；全部使用假响应。"""
import sys
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "美团OTA数据采集代码"))
import meituan_promotion_status_checks as checks


def frame(host, body="", states=(), tags=(), button=False):
    result = MagicMock(url=f"https://{host}/test")

    def locator(selector):
        loc = MagicMock()
        loc.count.return_value = int(button) if selector == ".pc-cpn-sign-button" else 0
        loc.first.is_visible.return_value = button
        loc.inner_text.return_value = body
        loc.all_text_contents.return_value = tags if "top-tag" in selector else states
        return loc

    result.locator.side_effect = locator
    return result


class PromotionStatusAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.page = MagicMock(url=checks.WORKBENCH_URL)
        self.page.frames = []
        self.callbacks = {}
        self.page.on.side_effect = lambda event, callback: self.callbacks.update({event: callback})
        for name, value in {"POI_ID": "123", "PARTNER_ID": "456", "PAGE_CHECK_SECONDS": 1}.items():
            self.stack.enter_context(patch.object(checks, name, value))
        self.stack.enter_context(patch.object(checks, "status_page", return_value=nullcontext(self.page)))
        self.stack.enter_context(patch.object(checks, "require_backend_session"))
        self.stack.enter_context(patch.object(checks, "wait_for_workbench_wrapper"))
        self.click = self.stack.enter_context(patch.object(checks, "click_workbench_menu"))

    def capture(self, url, payload, http=200):
        response = SimpleNamespace(url=url, status=http, json=lambda: payload)
        self.click.side_effect = lambda *args: self.callbacks["response"](response)

    @staticmethod
    def invoice_payload(**updates):
        return {"status": 0, "data": {"poiIdStr": "123", "partnerId": 456, "bizType": 1, **updates}}

    def test_invoice_validates_business_state_and_hotel(self):
        self.assertEqual(checks.invoice_status(self.invoice_payload()), "OPEN")
        self.assertEqual(checks.invoice_status(self.invoice_payload(bizType=2)), "CLOSED")
        self.assertEqual(checks.invoice_status(self.invoice_payload(bizType=0)), "CLOSED")
        for updates in ({"bizType": None}, {"bizType": True}, {"bizType": "1"},
                        {"poiIdStr": "other"}, {"partnerId": 999}):
            with self.subTest(updates=updates), self.assertRaises(RuntimeError):
                checks.invoice_status(self.invoice_payload(**updates))
        for payload in (None, {}, {"status": 1, "data": self.invoice_payload()["data"]},
                        {"status": 0, "data": {"poiId": 123, "partnerId": 456, "qualification": 1}}):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                checks.invoice_status(payload)

    def test_invoice_uses_one_readonly_request_in_existing_eb_frame(self):
        eb = frame("eb.meituan.com")
        eb.evaluate.return_value = {"http": 200, "payload": self.invoice_payload()}
        self.page.frames = [eb]
        with patch.object(checks, "find_visible_text", return_value=True):
            self.assertEqual(checks.fetch_scheduled_invoice_status(), "OPEN")
        script, ids = eb.evaluate.call_args.args
        self.assertIn("/api/v1/ebooking/invoice/find", script)
        self.assertIn("method: 'GET'", script)
        self.assertIn("credentials: 'include'", script)
        self.assertIn("AbortSignal.timeout(20000)", script)
        self.assertEqual(ids, {"poiId": "123", "partnerId": "456"})
        eb.evaluate.assert_called_once()
        self.page.goto.assert_not_called()

    def test_invoice_failures_never_fall_back_to_open_or_retry(self):
        eb = frame("eb.meituan.com", body="酒店发票信息维护")
        self.page.frames = [eb]
        for response in ({"http": 403, "payload": None}, {"http": 200, "payload": {"status": 1}},
                         {"http": 200, "payload": self.invoice_payload(poiIdStr="other")}):
            eb.evaluate.reset_mock()
            eb.evaluate.return_value = response
            with self.subTest(response=response), patch.object(checks, "find_visible_text", return_value=True):
                with self.assertRaises(RuntimeError):
                    checks.fetch_scheduled_invoice_status()
            eb.evaluate.assert_called_once()

    def test_invoice_does_not_run_in_wrong_origin_or_without_business_page(self):
        other = frame("other.example")
        self.page.frames = [other]
        with patch.object(checks, "find_visible_text", return_value=True), self.assertRaises(RuntimeError):
            checks.fetch_scheduled_invoice_status()
        other.evaluate.assert_not_called()
        eb = frame("eb.meituan.com")
        self.page.frames = [eb]
        with patch.object(checks, "find_visible_text", return_value=None), self.assertRaises(RuntimeError):
            checks.fetch_scheduled_invoice_status()
        eb.evaluate.assert_not_called()

    def test_welfare_does_not_classify_arbitrary_content_as_closed(self):
        for text in ("公益流量服务说明", "请登录后查看公益流量", "正在加载", "其他酒店已加入计划"):
            self.assertIsNone(checks.public_welfare_page_status([], text))
        self.assertEqual(checks.public_welfare_page_status([" 生效中 "], ""), "OPEN")
        self.assertEqual(checks.public_welfare_page_status([], "权益生效中"), "OPEN")
        self.assertEqual(checks.public_welfare_page_status(["已过期", "未生效"], ""), "CLOSED")
        self.assertIsNone(checks.public_welfare_page_status(["未知状态"], ""))

    def test_welfare_requires_explicit_successful_unsigned_modes(self):
        payload = {"status": 0, "signStatus": {"donationMode": {"ORDER": "INVALID", "CASH": "NOT_EXIST"}}}
        self.assertEqual(checks.public_welfare_merchant_status(payload), "CLOSED")
        for invalid in (None, {}, {"status": 1}, {"status": 0},
                        {"status": 0, "signStatus": {"donationMode": {"ORDER": "INVALID"}}},
                        {"status": 0, "signStatus": {"donationMode": {"ORDER": "VALID", "CASH": "INVALID"}}}):
            self.assertIsNone(checks.public_welfare_merchant_status(invalid))

    def test_welfare_unsigned_api_and_visible_enrollment_component_are_both_required(self):
        payload = {"status": 0, "signStatus": {"donationMode": {"ORDER": "INVALID", "CASH": "NOT_EXIST"}}}
        for host, http, button in (("gongyi.meituan.com", 200, True), ("other.example", 200, True),
                                   ("gongyi.meituan.com", 500, True), ("gongyi.meituan.com", 200, False)):
            self.page.frames = [frame("gongyi.meituan.com", button=button)]
            self.capture(f"https://{host}/api/SuggestSignFacade_queryMerchantInfo", payload, http)
            with self.subTest(host=host, http=http, button=button):
                if host == "gongyi.meituan.com" and http == 200 and button:
                    self.assertEqual(checks.fetch_public_welfare_status(), "CLOSED")
                else:
                    with self.assertRaises(RuntimeError):
                        checks.fetch_public_welfare_status()

    def test_highlights_api_requires_matching_hotel_and_success(self):
        for poi, http in (("123", 200), ("other", 200), ("123", 403)):
            self.capture(f"https://tdc.meituan.com/gw/api/publish/eb/highlight/query?poiId={poi}",
                         {"code": 0, "data": None}, http)
            with self.subTest(poi=poi, http=http):
                if poi == "123" and http == 200:
                    self.assertEqual(checks.fetch_hotel_highlights_status(), "CLOSED")
                else:
                    with self.assertRaises(RuntimeError):
                        checks.fetch_hotel_highlights_status()

    def test_highlights_errors_are_not_treated_as_empty(self):
        for invalid in (None, {}, {"code": 401, "data": None}, {"code": 0}):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                checks.highlight_query_status(invalid)
        self.assertIsNone(checks.highlight_query_status({"code": 0, "data": {"total": 1}}))

    def test_highlights_page_requires_real_cards_not_url_parameters(self):
        self.page.url += "?hasVpoiSelect=true"
        self.page.frames = [frame("other.example", tags=["在线"])]
        with self.assertRaises(RuntimeError):
            checks.fetch_hotel_highlights_status()
        for tags, expected in ((["在线"], "OPEN"), (["待提交", "驳回"], "CLOSED")):
            self.page.frames = [frame("tdc.meituan.com", tags=tags)]
            self.assertEqual(checks.fetch_hotel_highlights_status(), expected)

    def test_login_page_is_unknown_not_closed(self):
        self.page.frames = [frame("epassport.meituan.com")]
        with self.assertRaisesRegex(RuntimeError, "内页未登录"):
            checks.fetch_public_welfare_status()


if __name__ == "__main__":
    unittest.main()
