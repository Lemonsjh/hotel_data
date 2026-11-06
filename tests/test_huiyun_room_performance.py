"""慧云房类日报当日列、未归属收入和事务范围。"""
import base64
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'OTA采集服务')]
from huiyun_pms import room_performance_report as report
from huiyun_pms import room_performance_storage as storage
import collection_schema

DAY, NOW = date(2026, 11, 4), datetime(2026, 11, 5, 17)
CONTEXT = {'hotel_code': 'native', 'hotel_name': '测试酒店'}


def fixture():
    row = [''] * 36
    for index, value in {0: '在店房数', 1: 2, 3: 999, 11: 'dayuse', 17: '大床房', 18: '占房率',
                         19: 50, 20: 999, 24: '房费总计', 25: 400, 26: 9999, 30: '平均房价',
                         31: 200, 32: 9999}.items():
        row[index] = value
    unknown = row.copy(); unknown[17] = ''; unknown[1] = 0; unknown[19] = 0; unknown[25] = 20; unknown[31] = 0
    total = [''] * 31
    for index, value in {0: '合计', 1: '在店房数', 2: 2, 13: '占房率', 19: '房费总计', 20: 420}.items():
        total[index] = value
    return [['测试酒店', '房类分析报告日报(权责发生)\n'], ['日累计(TODAY)', '月累计(M-T-D)'], row, unknown, total]


def parsed(cells=None):
    return report.parse_cells(cells or fixture(), 'hotel', CONTEXT, DAY, NOW)


class HuiyunRoomPerformanceTests(unittest.TestCase):
    def test_only_daily_columns_and_platform_occupancy_are_used(self):
        records, diagnostic = parsed()
        self.assertEqual(len(records), 1)
        row = records[0]
        self.assertEqual((row['room_nights'], row['room_revenue'], row['adr'], row['occupancy_rate'], row['revpar']),
                         (2, 400, 200, 50, 100))
        self.assertEqual(row['pms_rate_room_type_id'], '')
        self.assertIsNone(row['room_type_id'])
        self.assertEqual(diagnostic['excluded_room_revenue'], 20)

    def test_dummy_and_unassigned_amounts_are_never_spread_over_real_types(self):
        data = fixture(); data[3][17] = '哑房'
        records, diagnostic = parsed(data)
        self.assertEqual(records[0]['room_revenue'], 400)
        self.assertEqual(diagnostic['excluded_room_revenue'], 20)

    def test_zero_days_and_fractional_nights_preserve_native_values(self):
        data = fixture(); data[2][1] = 2.25; data[4][2] = 2.25
        self.assertEqual(parsed(data)[0][0]['room_nights'], Decimal('2.2500'))
        data[2][1] = 0; data[2][19] = 0; data[2][31] = 0; data[4][2] = 0
        self.assertEqual(parsed(data)[0][0]['revpar'], 0)

    def test_wrong_hotel_layout_duplicate_total_and_missing_value_fail(self):
        for row, col, value in [(0, 0, '其他酒店'), (2, 24, '月累计'), (2, 25, ''),
                                 (2, 1, 'NaN'), (4, 20, 421), (3, 17, '大床房')]:
            data = fixture(); data[row][col] = value
            with self.subTest(row=row, col=col), self.assertRaises(ValueError):
                parsed(data)
        with self.assertRaises(ValueError):
            parsed(fixture()[:-1])

    def test_envelope_dates_and_hotel_are_checked(self):
        payload = {'ok': True, **CONTEXT, 'business_date': str(DAY), 'start_date': str(DAY), 'end_date': str(DAY),
                   'report_base64': base64.b64encode(b'fake').decode()}
        sheet = MagicMock(nrows=len(fixture())); sheet.row_values.side_effect = fixture()
        book = MagicMock(); book.sheets.return_value = [sheet]
        with patch.object(report.xlrd, 'open_workbook', return_value=book):
            self.assertEqual(report.transform(payload, 'hotel', CONTEXT, DAY, NOW)[0][0]['room_revenue'], 400)
        for key in ('hotel_code', 'hotel_name', 'business_date', 'start_date', 'end_date'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                report.transform({**payload, key: 'wrong'}, 'hotel', CONTEXT, DAY, NOW)

    def test_storage_exact_alias_scope_and_retention(self):
        conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[], [('大床房', 'mapped')]]
        storage.sync_mysql(parsed()[0], 'hotel', [DAY], NOW, conn)
        self.assertEqual(cur.executemany.call_args.args[1][0]['room_type_id'], 'mapped')
        self.assertEqual(cur.execute.call_args_list[2].args[1], ('hotel', storage.SOURCE, DAY))
        self.assertEqual(cur.execute.call_args.args[1], ('hotel', storage.SOURCE, date(2025, 10, 2)))
        conn.commit.assert_called_once()

    def test_other_pms_newer_batch_and_insert_failure_preserve_old_data(self):
        for source, stamp in [('PMS（美团）', NOW), (storage.SOURCE, datetime(2026, 11, 5, 18))]:
            conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = [(source, stamp)]
            with self.assertRaises(ValueError): storage.sync_mysql(parsed()[0], 'hotel', [DAY], NOW, conn)
            conn.rollback.assert_called_once(); cur.executemany.assert_not_called()
        conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[], []]; cur.executemany.side_effect = RuntimeError('failed')
        with self.assertRaises(RuntimeError): storage.sync_mysql(parsed()[0], 'hotel', [DAY], NOW, conn)
        conn.rollback.assert_called_once(); conn.commit.assert_not_called()

    def test_first_seven_days_then_yesterday_daily_skip_and_dry_run(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, \
                patch.dict('os.environ', {'HOTEL_ID': 'hotel', 'HOTEL_OTA_MYSQL_DATABASE': 'fake', 'HOTEL_OTA_OUTPUT_DIR': directory}), \
                patch.object(sys, 'argv', ['room_performance_report.py']), patch.object(report, 'now_shanghai', return_value=NOW), \
                patch.object(report, 'request', return_value=CONTEXT) as request, \
                patch.object(report, 'transform', return_value=parsed()), patch.object(report.time, 'sleep'), \
                patch.object(report, 'sync_mysql') as sync, patch('builtins.print'):
            sync.side_effect = RuntimeError('failed')
            with self.assertRaises(RuntimeError): report.main()
            marker = Path(directory) / 'huiyun/jl01_checkpoints.json'
            self.assertFalse(marker.exists())
            sync.side_effect = None; sync.reset_mock(); report.main()
            self.assertEqual(sync.call_args.args[2], [date(2026, 10, 29 + n) if n < 3 else date(2026, 11, n - 2) for n in range(7)])
            request.reset_mock(); report.main(); self.assertEqual(request.call_count, 1)
            with patch.object(sys, 'argv', ['room_performance_report.py', '--force']): report.main()
            self.assertEqual(sync.call_args.args[2], [DAY])
            previous = marker.read_bytes()
            with patch.object(sys, 'argv', ['room_performance_report.py', '--dry-run']): report.main()
            self.assertEqual(sync.call_count, 2); self.assertEqual(marker.read_bytes(), previous)

    def test_required_tables(self):
        self.assertEqual(collection_schema.required_tables('pms_fetch', [('huiyun', ROOT / 'huiyun_pms/room_performance_report.py', [])]),
                         {storage.TABLE, 'hotel_room_type_mapping'})


if __name__ == '__main__':
    unittest.main()
