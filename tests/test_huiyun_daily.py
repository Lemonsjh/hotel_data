"""慧云日报只取当日数据、首批窗口和事务失败保护。"""
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
from huiyun_pms import daily_report as daily
import collection_schema

CONTEXT = {'hotel_code': 'test-code', 'hotel_name': '测试酒店'}
DAY, NOW = date(2026, 11, 4), datetime(2026, 11, 5, 15)


def report_cells():
    return [['营业状况报表', '酒店名称：测试酒店'], ['营业项目', '当日', '月累计', '年累计'],
            ['客房总数', 149, '', 9999], ['    财务间夜数', '', 112, '', 8888],
            ['房费', '', 25760.42, 72667.14, 4872125.59],
            ['    主楼', '', 25760.42], ['    哑房楼区', '', 22.44],
            ['平均房价', 230.00375, 300], ['报表条件:', '统计日期:2026-11-04']]


def payload():
    return {'ok': True, **CONTEXT, 'business_date': str(DAY),
            'report_base64': base64.b64encode(b'workbook').decode()}


def parsed(cells=None, data=None):
    cells = report_cells() if cells is None else cells
    sheet = MagicMock(nrows=len(cells)); sheet.row_values.side_effect = cells
    book = MagicMock(); book.sheets.return_value = [sheet]
    with patch.object(daily.xlrd, 'open_workbook', return_value=book):
        return daily.transform(data or payload(), 'hotel', CONTEXT, DAY, NOW)


class HuiyunDailyTests(unittest.TestCase):
    def test_only_day_column_is_used_and_raw_adr_drives_revpar(self):
        row = parsed()
        self.assertEqual(row['room_count'], 149)
        self.assertEqual(row['room_nights'], Decimal('112.00'))
        self.assertEqual(row['room_revenue'], Decimal('25760.42'))
        self.assertEqual(row['occupancy_rate'], Decimal('75.17'))
        self.assertEqual(row['adr'], Decimal('230.00'))
        self.assertEqual(row['revpar'], Decimal('172.89'))
        self.assertEqual(row['dimension_type'], '总营业指标')
        self.assertIsNone(row['sold_rooms'])

    def test_fractional_nights_and_zero_nights_are_preserved(self):
        cells = report_cells(); cells[3][2] = 112.25
        self.assertEqual(parsed(cells)['room_nights'], Decimal('112.25'))
        cells[3][2] = 0; cells[4][2] = 0; cells[7][1] = 0
        row = parsed(cells)
        self.assertEqual((row['occupancy_rate'], row['adr'], row['revpar']), (0, 0, 0))

    def test_missing_day_value_never_falls_back_to_cumulative_column(self):
        cells = report_cells(); cells[3][2] = ''
        with self.assertRaisesRegex(ValueError, '当日值'):
            parsed(cells)

    def test_missing_duplicate_and_nonfinite_metrics_fail(self):
        for cells in (report_cells()[:7] + report_cells()[8:],
                      report_cells() + [['平均房价', 230]],
                      report_cells()[:7] + [['平均房价', 'NaN']] + report_cells()[8:]):
            with self.subTest(cells=cells), self.assertRaises(ValueError):
                parsed(cells)

    def test_report_hotel_day_and_header_must_match(self):
        for row, col, value in ((0, 1, '酒店名称：其他酒店'), (8, 1, '统计日期:2026-11-03'),
                                (1, 1, '月累计')):
            cells = report_cells(); cells[row][col] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                parsed(cells)
        data = payload(); data['hotel_code'] = 'other'
        with self.assertRaises(ValueError):
            parsed(data=data)

    def test_first_seven_days_and_later_only_yesterday(self):
        first = daily.collection_dates(NOW.date(), False)
        self.assertEqual((len(first), first[0], first[-1]), (7, date(2026, 10, 29), DAY))
        self.assertEqual(daily.collection_dates(NOW.date(), True), [DAY])
        self.assertEqual(daily.collection_dates(date(2024, 3, 1), True), [date(2024, 2, 29)])

    def test_storage_scopes_upsert_and_retention_in_one_transaction(self):
        conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        daily.sync_mysql([parsed()], 'hotel', [DAY], NOW, conn)
        conn.commit.assert_called_once()
        self.assertEqual(cur.execute.call_args_list[0].args[1],
                         ('测试酒店', daily.SOURCE, DAY, DAY, '总营业指标', '总营业指标'))
        self.assertIn('ON DUPLICATE KEY UPDATE', cur.executemany.call_args.args[0])
        self.assertEqual(cur.execute.call_args.args[1], ('hotel', daily.SOURCE, date(2025, 10, 2)))
        cur.executemany.side_effect = RuntimeError('database failure')
        with self.assertRaises(RuntimeError):
            daily.sync_mysql([parsed()], 'hotel', [DAY], NOW, conn)
        conn.rollback.assert_called_once()

    def test_conflicting_hotel_or_newer_snapshot_rolls_back(self):
        for existing in (('other', NOW), ('hotel', datetime(2026, 11, 5, 16))):
            conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = [existing]
            with self.subTest(existing=existing), self.assertRaises(ValueError):
                daily.sync_mysql([parsed()], 'hotel', [DAY], NOW, conn)
            cur.executemany.assert_not_called(); conn.rollback.assert_called_once()

    def test_failed_collection_does_not_write_or_mark_initial_backfill(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, \
                patch.dict('os.environ', {'HOTEL_ID': 'hotel', 'HOTEL_OTA_MYSQL_DATABASE': 'fake',
                                         'HOTEL_OTA_OUTPUT_DIR': directory}), \
                patch.object(sys, 'argv', ['daily_report.py']), patch.object(daily, 'now_shanghai', return_value=NOW), \
                patch.object(daily, 'request', side_effect=[CONTEXT, RuntimeError('report failed')]), \
                patch.object(daily, 'sync_mysql') as sync:
            with self.assertRaises(RuntimeError):
                daily.main()
            sync.assert_not_called()
            self.assertFalse((Path(directory) / 'huiyun/jy01_checkpoints.json').exists())

    def test_success_marker_is_scoped_and_next_run_queries_only_yesterday(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, \
                patch.dict('os.environ', {'HOTEL_ID': 'hotel', 'HOTEL_OTA_MYSQL_DATABASE': 'fake',
                                         'HOTEL_OTA_OUTPUT_DIR': directory}), \
                patch.object(sys, 'argv', ['daily_report.py']), patch.object(daily, 'now_shanghai', return_value=NOW), \
                patch.object(daily, 'request', return_value=CONTEXT) as request, \
                patch.object(daily, 'transform', return_value=parsed()), patch.object(daily.time, 'sleep'), \
                patch.object(daily, 'sync_mysql') as sync:
            daily.main()
            self.assertEqual(len(sync.call_args.args[2]), 7)
            checkpoint = Path(directory) / 'huiyun/jy01_checkpoints.json'
            self.assertEqual(len(json.loads(checkpoint.read_text(encoding='utf-8'))), 1)
            request.reset_mock(); daily.main()
            sync.assert_called_once()
            self.assertEqual(request.call_args_list[0].args, ('booking_context',))
            self.assertEqual(request.call_count, 1)
            request.reset_mock()
            with patch.object(sys, 'argv', ['daily_report.py', '--force']):
                daily.main()
            self.assertEqual(sync.call_args.args[2], [DAY])
            self.assertEqual(request.call_args_list[1].args, ('booking|2026-11-04|2026-11-04|daily',))
            before = checkpoint.read_bytes()
            with patch.object(sys, 'argv', ['daily_report.py', '--dry-run']):
                daily.main()
            self.assertEqual(checkpoint.read_bytes(), before)
            self.assertEqual(sync.call_count, 2)

    def test_daily_entry_declares_standard_table(self):
        tables = collection_schema.required_tables('pms_fetch', [('huiyun', ROOT / 'huiyun_pms/daily_report.py', [])])
        self.assertEqual(tables, {daily.TABLE, 'hotel_room_type_mapping'})


if __name__ == '__main__':
    unittest.main()
