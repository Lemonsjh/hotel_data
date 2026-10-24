from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "携程OTA数据采集代码"))
import ctrip_business_travel_status as business
import ctrip_promotion_status_data as collector


def payload(actions=None, statuses=None):
    return {
        "resStatus": {"rcode": 200}, "ResponseStatus": {"Ack": "Success", "Errors": []},
        "dtos": [{"promotionType": "BTP", "campaignName": "商旅专享",
                  "actions": ["ADD"] if actions is None else actions,
                  "promotionStatusList": [] if statuses is None else statuses}],
    }


def results(business_result):
    values = {key: (0, None) for key in (
        "points_alliance", "preferred_club", "travel_photo", "listing_short_tags",
        "listing_recommendation_words", "quick_check_inn",
    )}
    values.update(business_travel=business_result, hourly_room=((0, 0), None), information=(96, None))
    return values


class CtripBusinessTravelTests(unittest.TestCase):
    def test_only_exact_add_and_explicit_empty_status_list_mean_not_joined(self):
        self.assertEqual(business.business_travel_from_payload(payload()),
                         {"enabled": 0, "status": "NOT_JOINED", "status_detail": "ADD"})
        for actions in ([], ["VIEW"], ["ADDMORE"], ["ADD", "VIEW"], ["ADD", "ADD"]):
            with self.subTest(actions=actions), self.assertRaises(RuntimeError):
                business.business_travel_from_payload(payload(actions))

    def test_effective_is_joined_and_retains_all_known_states(self):
        for states in (["EFFECTIVE"], ["PAUSE", "EFFECTIVE", "AUDIT"]):
            result = business.business_travel_from_payload(payload(["VIEW"], states))
            self.assertEqual((result["enabled"], result["status"]), (1, "JOINED"))
            self.assertEqual(result["status_detail"], "|".join(sorted(set(states))))

    def test_paused_audit_and_expired_are_not_conflated_with_not_joined(self):
        for states in (["PAUSE"], ["AUDIT"], ["EXPIRED"], ["PAUSE", "AUDIT"]):
            result = business.business_travel_from_payload(payload(["VIEW"], states))
            self.assertEqual(result["enabled"], 0)
            self.assertEqual(result["status"], "|".join(sorted(set(states))))
            self.assertNotEqual(result["status"], "NOT_JOINED")

    def test_unknown_or_conflicting_states_are_rejected(self):
        for actions, states in ((["VIEW"], ["UNKNOWN"]), (["VIEW"], ["EFFECTIVE", "NEW"]),
                                (["ADD"], ["EFFECTIVE"]), (["ADD"], ["PAUSE"])):
            with self.subTest(actions=actions, states=states), self.assertRaises(RuntimeError):
                business.business_travel_from_payload(payload(actions, states))

    def test_missing_or_malformed_lists_are_not_inferred_as_empty(self):
        for key in ("actions", "promotionStatusList"):
            data = payload()
            del data["dtos"][0][key]
            with self.assertRaises(RuntimeError):
                business.business_travel_from_payload(data)
            for invalid in (None, "ADD", {}, [None], [1], [""]):
                data["dtos"][0][key] = invalid
                with self.subTest(key=key, invalid=invalid), self.assertRaises(RuntimeError):
                    business.business_travel_from_payload(data)

    def test_missing_duplicate_and_legacy_cards_do_not_prove_not_joined(self):
        for cards in (None, {}, [], [None], [{"promotionType": "BT1", "actions": ["ADD"]}],
                      payload()["dtos"] * 2):
            data = payload()
            data["dtos"] = cards
            data["historyTypeInfos"] = [{"promotionType": "BTP", "promotionName": "商旅专享"}]
            with self.subTest(cards=cards), self.assertRaises(RuntimeError):
                business.business_travel_from_payload(data)

    def test_unrelated_cards_and_record_order_do_not_change_btp_matching(self):
        data = payload()
        data["dtos"].insert(0, {"promotionType": "BT1", "actions": ["VIEW"], "promotionStatusList": ["EFFECTIVE"]})
        data["dtos"].append({"promotionType": "EVD"})
        self.assertEqual(business.business_travel_from_payload(data)["status"], "NOT_JOINED")

    def test_unsuccessful_response_is_not_treated_as_unjoined(self):
        for invalid in (None, [], {}, {"resStatus": {"rcode": 500}},
                        {"resStatus": {"rcode": 0}}, {"resStatus": {"rcode": True}}):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                business.business_travel_from_payload(invalid)
        for service in ({"Ack": "Failure"}, {"Ack": "Success", "Errors": [{"code": "error"}]}, None):
            data = payload()
            data["ResponseStatus"] = service
            with self.assertRaises(RuntimeError):
                business.business_travel_from_payload(data)

    def page(self, response_payload=None, http=200):
        page = MagicMock()
        response = MagicMock()
        response.status = http
        response.json.return_value = payload() if response_payload is None else response_payload
        page.expect_response.return_value.__enter__.return_value.value = response
        return page, response

    def test_observes_page_request_without_signing_up_or_constructing_post(self):
        page, response = self.page()
        self.assertEqual(business.business_travel_status(page)["status"], "NOT_JOINED")
        page.goto.assert_called_once_with(business.PROMOTION_HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        predicate = page.expect_response.call_args.args[0]
        self.assertTrue(predicate(SimpleNamespace(url="https://ebooking.ctrip.com" + business.PROMOTION_HOME_PATH + "?_=1")))
        self.assertFalse(predicate(SimpleNamespace(url="https://example.com" + business.PROMOTION_HOME_PATH)))
        self.assertFalse(predicate(SimpleNamespace(url="https://ebooking.ctrip.com" + business.PROMOTION_HOME_PATH + "Extra")))
        page.get_by_text.assert_not_called()
        page.evaluate.assert_not_called()
        page.locator.assert_not_called()

    def test_http_errors_non_json_and_timeouts_have_no_legacy_text_fallback(self):
        for http in (302, 401, 429, 500):
            page, _ = self.page(http=http)
            with self.subTest(http=http), self.assertRaises(RuntimeError):
                business.business_travel_status(page)
        page, response = self.page()
        page.locator.return_value.inner_text.return_value = "商旅专享说明 企业间对公结算"
        response.json.side_effect = ValueError("not JSON")
        with self.assertRaises(RuntimeError):
            business.business_travel_status(page)
        page.locator.assert_not_called()
        page, _ = self.page()
        page.goto.side_effect = TimeoutError("timeout")
        with self.assertRaises(TimeoutError):
            business.business_travel_status(page)
        self.assertEqual(page.goto.call_count, 1)

    def test_result_dict_enabled_zero_does_not_become_truthy_joined(self):
        for actions, states in ((["ADD"], []), (["VIEW"], ["PAUSE"]), (["VIEW"], ["EFFECTIVE"])):
            value = business.business_travel_from_payload(payload(actions, states))
            rows = collector.status_rows("hotel-1", datetime(2026, 10, 24), results((value, None)))
            row = next(row for row in rows if row[3] == "business_travel_price")
            self.assertEqual((row[5], row[6], row[7]), (value["enabled"], value["status"], value["status_detail"]))

    def test_unknown_status_is_skipped_while_other_rows_still_upsert(self):
        with patch("builtins.print"):
            failed = collector.collect_one("business_travel", lambda: business.business_travel_from_payload(payload(["VIEW"], [])))
            self.assertIsNone(failed[0])
            self.assertIsNotNone(failed[1])
            with patch.object(collector, "collect_statuses", return_value=results(failed)), \
                 patch.object(collector, "require_hotel_id", return_value="hotel-1"), \
                 patch.object(collector, "sync_metric_history_table") as writer, \
                 patch.object(collector, "write_output"):
                self.assertEqual(collector.main(), 0)
        rows = writer.call_args.args[2]
        self.assertEqual(len(rows), 8)
        self.assertNotIn("business_travel_price", [row[3] for row in rows])
        self.assertEqual(writer.call_args.kwargs["retention_days"], None)

    def test_preferred_club_keeps_existing_page_status_detection(self):
        page = MagicMock()
        page.locator.return_value.all.return_value = []
        page.locator.return_value.inner_text.return_value = collector.PREFERRED_CLUB_PAGE_MARKERS[0]
        with patch.object(collector, "open_promotion_page"), patch.object(collector, "ensure_logged_in"):
            value = collector.activity_enabled(page, collector.PREFERRED_CLUB_MENU_POINT,
                                               collector.PREFERRED_CLUB_PAGE_MARKERS, "button", "立即报名")
        self.assertEqual(value, 1)


if __name__ == "__main__":
    unittest.main()
