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
        for value in (None, '', 'NaN', 'bad', '1,23.45', '1,2', '--', True):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                balance.build_row('meituan', {'code': 200, 'msg': {'balance': value}}, 'hotel', self.now)

    def test_formatted_balance_preserves_amount_and_zero(self):
        for value, expected in [('1,234.56', '1234.56'), (' 12,345,678.90 ', '12345678.90'),
                                ('-1,234.56', '-1234.56'), ('0.00', '0.00')]:
            with self.subTest(value=value):
                row = balance.build_row('meituan', {'code': 200, 'msg': {'balance': value}}, 'hotel', self.now)
                self.assertEqual(row[4], Decimal(expected))

    def test_invalid_amount_does_not_replace_snapshot(self):
        with patch.object(balance, 'sync_table') as sync, patch.dict('os.environ', {'HOTEL_ID': 'hotel'}):
            with self.assertRaisesRegex(RuntimeError, '无效金额'):
                balance.main('meituan', lambda: {'code': 200, 'msg': {'balance': '--'}})
        sync.assert_not_called()

    def test_requests_match_platform_interfaces(self):
        with patch.dict('os.environ', {'CTRIP_COOKIE': 'fake', 'MEITUAN_PROMOTION_COOKIE': 'fake'}), \
             patch.object(balance.requests, 'post') as post, patch.object(balance.requests, 'get') as get:
            balance.fetch_balance('ctrip')
            self.assertEqual(post.call_args.kwargs['data'], {'accountType': 'Hotel', 'specialAccountType': 'Pyramid'})
            get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
