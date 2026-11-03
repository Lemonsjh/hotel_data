from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'OTA采集服务')]
import promotion_balance as balance


class PromotionBalanceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 12)

    def test_meituan_balance_preserves_zero_and_missing_breakdown(self):
        row = balance.build_row('meituan', {'code': 200, 'msg': {'balance': '0.00'}}, 'hotel', self.now)
        self.assertEqual(row, ['hotel', 'meituan', 'promotion_tong', '推广通', Decimal('0.00'),
                               None, None, 'CNY', self.now])

    def test_ctrip_retains_three_values_and_type(self):
        data = {'specialAccountType': 'Pyramid', 'totalBalance': '12.34',
                'accountBalance': '10.01', 'couponBalance': '2.33', 'currency': 'CNY'}
        row = balance.build_row('ctrip', {'code': 0, 'data': data}, 'hotel', self.now)
        self.assertEqual(row[2:8], ['pyramid', '金字塔', Decimal('12.34'), Decimal('10.01'),
                                    Decimal('2.33'), 'CNY'])

    def test_failure_does_not_write_or_replace_existing_snapshot(self):
        with patch.object(balance, 'fetch_balance', return_value={'code': 0, 'msg': 'expired'}), \
             patch.object(balance, 'sync_table') as sync, patch.dict('os.environ', {'HOTEL_ID': 'hotel'}):
            with self.assertRaises(RuntimeError):
                balance.main('meituan')
            sync.assert_not_called()

    def test_missing_or_invalid_total_fails(self):
        for value in (None, '', 'NaN', 'bad'):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                balance.build_row('meituan', {'code': 200, 'msg': {'balance': value}}, 'hotel', self.now)

    def test_requests_match_platform_interfaces(self):
        with patch.dict('os.environ', {'CTRIP_COOKIE': 'fake', 'MEITUAN_PROMOTION_COOKIE': 'fake'}), \
             patch.object(balance.requests, 'post') as post, patch.object(balance.requests, 'get') as get:
            balance.fetch_balance('ctrip')
            self.assertEqual(post.call_args.kwargs['data'], {'accountType': 'Hotel', 'specialAccountType': 'Pyramid'})
            get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
