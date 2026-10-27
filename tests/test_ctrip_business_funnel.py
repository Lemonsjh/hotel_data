"""昨日漏斗四层人数、三段转化率及完整性校验。"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '携程OTA数据采集代码'))
import ctrip_business_data as business


class BusinessFunnelTests(unittest.TestCase):
    def setUp(self):
        self.stamp = datetime(2026, 10, 27, 10)
        self.day = self.stamp.date() - timedelta(days=1)
        self.hotel = dict(date=str(self.day), hotelId=123, listExposure=342, detailExposure=79,
                          orderFillingNum=8, orderSubmitNum=5)
        self.peer = dict(self.hotel, hotelId=-1, listExposure=353, detailExposure=66,
                         orderFillingNum=6, orderSubmitNum=3, flowRate=18.63)
        self.previous = dict(self.hotel, date='2026-10-19', listExposure=376, detailExposure=88,
                             orderFillingNum=6, orderSubmitNum=3)
        self.payload = {'flow_funnel': [self.hotel, self.peer, self.previous],
                        'flow_data': {'dataList': [{'indexType': 8, 'val': 8, 'rankComp': 6}]}}

    def rows(self, payload=None):
        rows = business.flow_data_rows(self.payload if payload is None else payload, self.stamp, self.day, '测试酒店')
        return {r[2]: dict(zip(business.HEADERS, r)) for r in rows}

    def test_four_stages_and_three_rates_match_page(self):
        rows = self.rows()
        self.assertEqual({key: r['metric_value'] for key, r in rows.items()}, {
            'list_page_exposure_count': 342, 'detail_page_visitor_count': 79,
            'order_page_visitor_count': 8, 'order_submit_count': 5,
            'exposure_conversion_rate': 23.1, 'order_conversion_rate': 10.13,
            'transaction_conversion_rate': 62.5})
        self.assertEqual(rows['order_page_visitor_count']['metric_name'], '订单页访客量')
        self.assertEqual(rows['order_submit_count']['metric_name'], '订单提交人数')
        self.assertEqual(rows['order_page_visitor_count']['competitor_rank'], 6)
        self.assertIsNone(rows['order_submit_count']['competitor_rank'])
        self.assertTrue(all(r['business_date'] == self.day for r in rows.values()))

    def test_peer_rates_use_same_displayed_funnel_counts(self):
        rows = self.rows()
        self.assertEqual(rows['detail_page_visitor_count']['peer_average'], 66)
        self.assertEqual(rows['exposure_conversion_rate']['peer_average'], 18.7)
        self.assertEqual(rows['order_conversion_rate']['peer_average'], 9.09)
        self.assertEqual(rows['transaction_conversion_rate']['peer_average'], 50)

    def test_comparison_is_previous_week_and_does_not_reuse_order_page_value(self):
        rows = self.rows()
        self.assertEqual(rows['order_submit_count']['compare_value'], 3)
        self.assertEqual(rows['transaction_conversion_rate']['compare_value'], 50)
        self.assertTrue(all(r['compare_label'] == '较上周同期' for r in rows.values()))
        missing = self.rows({**self.payload, 'flow_funnel': [self.hotel, self.peer]})
        self.assertTrue(all(r['compare_value'] is None for r in missing.values()))

    def test_zero_counts_are_valid_but_zero_denominator_is_unknown(self):
        values = business.funnel_values(dict(self.hotel, listExposure=0, detailExposure=0,
                                             orderFillingNum=0, orderSubmitNum=0))
        self.assertEqual(values['order_submit_count'], 0)
        self.assertIsNone(values['exposure_conversion_rate'])
        self.assertIsNone(values['transaction_conversion_rate'])

    def test_invalid_or_missing_counts_are_not_silently_zeroed(self):
        for value in (None, '', 'NaN', 'Infinity', -1, 1.5):
            with self.subTest(value=value), self.assertRaises(business.CtripApiError):
                business.funnel_values(dict(self.hotel, orderSubmitNum=value))

    def test_missing_duplicate_or_wrong_date_funnel_fails(self):
        for items in (None, [], [self.hotel], [self.peer], [self.hotel, self.hotel, self.peer],
                      [dict(self.hotel, date='2026-10-25'), self.peer]):
            with self.subTest(items=items), self.assertRaises(business.CtripApiError):
                self.rows({**self.payload, 'flow_funnel': items})

    def test_request_covers_yesterday_and_previous_week(self):
        with patch.object(business.CtripBusinessClient, 'post_json', return_value={}) as post:
            business.CtripBusinessClient('').query_all()
        request = next(c for c in post.call_args_list if c.args[0] == business.ENDPOINTS['flow_funnel'])
        end = datetime.strptime(request.args[1]['endDate'], '%Y-%m-%d').date()
        start = datetime.strptime(request.args[1]['startDate'], '%Y-%m-%d').date()
        self.assertEqual(end - start, timedelta(days=7))
        self.assertEqual(end, datetime.now().date() - timedelta(days=1))
        self.assertEqual(request.args[1]['platform'], 'Ctrip')

    def test_duplicate_previous_week_or_malformed_response_fails(self):
        for items in ([*self.payload['flow_funnel'], self.previous],
                      [*self.payload['flow_funnel'], None], {'code': 401}):
            with self.subTest(items=items), self.assertRaises(business.CtripApiError):
                self.rows({**self.payload, 'flow_funnel': items})

    def test_new_funnel_stays_on_yesterday_not_realtime_hourly_snapshot(self):
        payload = {**self.payload, 'management_data_realtime': {'dataList': [{'indexType': 0, 'val': 9}]}}
        rows = business.normalize_rows(payload, self.stamp, '测试酒店')
        funnel = [r for r in rows if r[4] == '流量漏斗']
        self.assertEqual(len(funnel), 7)
        self.assertTrue(all(r[1] == self.day for r in funnel))
        hourly = business.build_hourly_metric_rows(payload, self.stamp, '测试酒店')
        self.assertEqual(len(hourly), 1)
        self.assertEqual(hourly[0][6], 'realtime_booking_order_count')

    def test_incomplete_funnel_stops_before_any_database_write(self):
        payload = {**self.payload, 'flow_funnel': [self.hotel]}
        with patch.object(business.CtripBusinessClient, 'query_all', return_value=payload), \
                patch.object(business, 'save_rows') as save, \
                patch.object(business, 'save_hourly_snapshot') as hourly, \
                patch.object(sys, 'argv', ['ctrip_business_data.py']):
            with self.assertRaises(business.CtripApiError):
                business.main()
        save.assert_not_called()
        hourly.assert_not_called()


if __name__ == '__main__':
    unittest.main()
