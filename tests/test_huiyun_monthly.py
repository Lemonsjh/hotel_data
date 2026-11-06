"""慧云月报按自然月汇总及事务/首次回补保护。"""
import base64
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'OTA采集服务')]
from huiyun_pms import monthly_report as monthly
import collection_schema

CONTEXT = {'hotel_code': 'code', 'hotel_name': '测试酒店'}
START, END, NOW = date(2026, 11, 1), date(2026, 11, 2), datetime(2026, 11, 5, 15)


def cells():
    return [['营业收入月报', '酒店名称：测试酒店'], monthly.HEADERS[:],
            ['01(日)', 100, 20, 20, 600, 30, 20, 2000, 2600, 6],
            ['02(一)', 200, 50, 40, 1200, 30, 25, 3000, 4200, 6],
            ['总计', 300, 70, 60, 1800], ['起始日期:2026-11-01']]


def parsed(data=None, payload=None):
    data = cells() if data is None else data
    sheet = MagicMock(nrows=len(data)); sheet.row_values.side_effect = data
    book = MagicMock(); book.sheets.return_value = [sheet]
    payload = payload or {'ok': True, **CONTEXT, 'start_date': str(START), 'end_date': str(END),
                          'report_base64': base64.b64encode(b'workbook').decode()}
    with patch.object(monthly.xlrd, 'open_workbook', return_value=book):
        return monthly.transform(payload, 'hotel', CONTEXT, START, END, NOW)


class HuiyunMonthlyTests(unittest.TestCase):
    def test_monthly_sums_financial_nights_without_averaging_daily_rates(self):
        last, = parsed()
        self.assertEqual(last['period_month'], '2026-11')
        self.assertEqual(last['room_count'], 300)
        self.assertEqual(last['room_nights'], Decimal('60.00'))
        self.assertEqual(last['room_revenue'], Decimal('1800.00'))
        self.assertEqual(last['occupancy_rate'], Decimal('20.00'))
        self.assertEqual(last['adr'], Decimal('30.00'))
        self.assertEqual(last['revpar'], Decimal('6.00'))
        self.assertIsNone(last['maintain_rooms'])

    def test_missing_out_of_order_days_or_bad_total_fail(self):
        cases = [cells()[:3] + cells()[4:], cells(), cells()]
        cases[1][2][0] = '01(一)'
        cases[2][4][4] = 1801
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parsed(data)

    def test_wrong_hotel_date_header_and_nonfinite_amount_fail(self):
        for row, col, value in ((0, 1, '酒店名称：其他酒店'), (5, 0, '起始日期:2026-10-30'),
                                (1, 3, '其他口径'), (2, 4, 'NaN')):
            data = cells(); data[row][col] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                parsed(data)

    def test_platform_monthly_nights_total_takes_precedence_over_rounded_days(self):
        data = cells(); data[4][3] = 60.1
        self.assertEqual(parsed(data)[0]['room_nights'], Decimal('60.10'))

    def test_explicit_zero_month_is_kept_and_no_room_data_month_is_skipped(self):
        data = cells()
        for index in (2, 3):
            data[index][3] = data[index][4] = 0
        data[4][3] = data[4][4] = 0
        rows = parsed(data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[-1]['adr'], 0)
        data[2][1] = data[3][1] = data[4][1] = 0
        self.assertEqual(parsed(data), [])

    def test_initial_three_months_plus_last_year_and_month_end_rollover(self):
        windows = monthly.collection_windows(NOW.date(), False)
        self.assertEqual([start.strftime('%Y-%m') for start, _ in windows],
                         ['2025-09', '2025-10', '2025-11', '2026-09', '2026-10', '2026-11'])
        self.assertEqual(windows[0], (date(2025, 9, 1), date(2025, 9, 30)))
        self.assertEqual(windows[2], (date(2025, 11, 1), date(2025, 11, 4)))
        self.assertEqual(windows[-1], (date(2026, 11, 1), date(2026, 11, 4)))
        self.assertEqual(monthly.collection_windows(NOW.date(), True),
                         [(date(2025, 11, 1), date(2025, 11, 4)), (date(2026, 11, 1), date(2026, 11, 4))])
        self.assertEqual(monthly.collection_windows(date(2024, 3, 1), True),
                         [(date(2023, 2, 1), date(2023, 2, 28)), (date(2024, 2, 1), date(2024, 2, 29))])
        first_day = monthly.collection_windows(date(2027, 1, 1), False)
        self.assertEqual([start.strftime('%Y-%m') for start, _ in first_day],
                         ['2025-11', '2025-12', '2026-11', '2026-12'])

    def test_initial_window_crosses_year_and_handles_leap_february(self):
        windows = monthly.collection_windows(date(2026, 1, 15), False)
        self.assertEqual([start.strftime('%Y-%m') for start, _ in windows],
                         ['2024-11', '2024-12', '2025-01', '2025-11', '2025-12', '2026-01'])
        self.assertEqual(windows[-1][1], date(2026, 1, 14))
        self.assertEqual(windows[2][1], date(2025, 1, 14))
        windows = monthly.collection_windows(date(2024, 2, 29), False)
        self.assertIn((date(2023, 2, 1), date(2023, 2, 28)), windows)
        self.assertEqual(windows[-1], (date(2024, 2, 1), date(2024, 2, 28)))

    def test_complete_month_uses_complete_last_year_month_even_when_leap_days_differ(self):
        self.assertEqual(monthly.collection_windows(date(2025, 3, 1), True),
                         [(date(2024, 2, 1), date(2024, 2, 29)), (date(2025, 2, 1), date(2025, 2, 28))])

    def test_storage_uses_month_scope_and_thirty_six_month_retention(self):
        conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        monthly.sync_mysql(parsed(), 'hotel', NOW, conn)
        conn.commit.assert_called_once()
        self.assertIn('period_month BETWEEN', cur.execute.call_args_list[0].args[0])
        self.assertEqual(cur.execute.call_args.args[1], ('hotel', monthly.SOURCE, '2023-12'))
        cur.executemany.side_effect = RuntimeError('write failed')
        with self.assertRaises(RuntimeError):
            monthly.sync_mysql(parsed(), 'hotel', NOW, conn)
        conn.rollback.assert_called_once()

    def test_backfill_failure_never_writes_partial_data_or_checkpoint(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, \
                patch.dict('os.environ', {'HOTEL_ID': 'hotel', 'HOTEL_OTA_MYSQL_DATABASE': 'fake', 'HOTEL_OTA_OUTPUT_DIR': directory}), \
                patch.object(sys, 'argv', ['monthly_report.py']), patch.object(monthly, 'now_shanghai', return_value=NOW), \
                patch.object(monthly, 'request', side_effect=[CONTEXT, {}, RuntimeError('query failed')]), \
                patch.object(monthly, 'transform', return_value=parsed()), patch.object(monthly.time, 'sleep'), \
                patch.object(monthly, 'sync_mysql') as sync:
            with self.assertRaises(RuntimeError):
                monthly.main()
            sync.assert_not_called()
            self.assertFalse((Path(directory) / 'huiyun/jy03_checkpoints.json').exists())

    def test_success_marker_switches_to_current_and_last_year_month_and_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, \
                patch.dict('os.environ', {'HOTEL_ID': 'hotel', 'HOTEL_OTA_MYSQL_DATABASE': 'fake', 'HOTEL_OTA_OUTPUT_DIR': directory}), \
                patch.object(sys, 'argv', ['monthly_report.py']), patch.object(monthly, 'now_shanghai', return_value=NOW), \
                patch.object(monthly, 'request', return_value=CONTEXT) as request, \
                patch.object(monthly, 'transform', return_value=parsed()), patch.object(monthly.time, 'sleep'), \
                patch.object(monthly, 'sync_mysql') as sync, patch('builtins.print'):
            monthly.main()
            self.assertEqual(sum(call.args[0].startswith('booking|') for call in request.call_args_list), 6)
            checkpoint = Path(directory) / 'huiyun/jy03_checkpoints.json'
            self.assertEqual(len(json.loads(checkpoint.read_text(encoding='utf-8'))), 1)
            request.reset_mock()
            monthly.main()
            sync.assert_called_once()
            self.assertEqual(request.call_count, 1)
            request.reset_mock()
            with patch.object(sys, 'argv', ['monthly_report.py', '--dry-run']):
                monthly.main()
            reports = [call.args[0] for call in request.call_args_list if call.args[0].startswith('booking|')]
            self.assertEqual(reports, ['booking|2025-11-01|2025-11-04|monthly',
                                       'booking|2026-11-01|2026-11-04|monthly'])
            sync.assert_called_once()

    def test_entry_declares_existing_standard_table(self):
        tables = collection_schema.required_tables('pms_fetch', [('huiyun', ROOT / 'huiyun_pms/monthly_report.py', [])])
        self.assertEqual(tables, {monthly.TABLE, 'hotel_room_type_mapping'})


if __name__ == '__main__':
    unittest.main()
