"""慧云JL11区间汇总、原生累计房日、跨年及快照事务。"""
import base64
from datetime import date, datetime
from decimal import Decimal as D
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'OTA采集服务')]
from huiyun_pms import classification_report as report
from huiyun_pms import classification_storage as storage
import collection_schema

NOW = datetime(2026, 11, 5, 17)
START, END = report.report_window(NOW.date())
CONTEXT = {'hotel_code': 'native', 'hotel_name': '测试酒店'}


def month_cells():
    header = ['房类分析报告月报(权责发生)', '酒店代码:测试酒店', f'起始日期:{START}', '月累计(M-T-D)']
    line = ['', 'DL', '大床房', '在店房数', 30, 0, 9999, 0, 999, 0, 999, 0]
    fee = ['', '', '', '房费总计', 3000, 0, 999999, 0, 99999, 0, 99999, 0]
    unknown = ['', '', '', '在店房数', 0, 0, 0, 0, 0, 0, 0, 0]
    unknown_fee = ['', '', '', '房费总计', 10, 0, 0, 0, 0, 0, 0, 0]
    total = [''] * 24
    for index, value in {0: '合计', 1: '在店房数', 2: 30, 16: '房费总计', 17: 3010}.items(): total[index] = value
    return [header, line, ['占房率'] + [''] * 11, fee, ['平均房价', 100] + [''] * 10, unknown, unknown_fee, total]


def daily_cells(nights=10, occupancy=50, revenue=1000):
    row = [''] * 36
    for index, value in {0: '在店房数', 5: nights, 17: '大床房', 18: '占房率', 21: occupancy,
                         24: '房费总计', 27: revenue}.items(): row[index] = value
    return [['测试酒店', '房类分析报告日报(权责发生)'], row]


def cumulative():
    return {date(2026, 10, 5): report.cumulative_metrics(daily_cells(), CONTEXT),
            END: report.cumulative_metrics(daily_cells(40, 50, 4000), CONTEXT)}


def rows():
    details, _ = report.period_metrics(month_cells(), CONTEXT, START)
    return report.build_rows(details, cumulative(), 'hotel', CONTEXT, START, END, NOW)


class HuiyunClassificationTests(unittest.TestCase):
    def test_raw_period_fields_ignore_year_and_comparison_columns(self):
        details, excluded = report.period_metrics(month_cells(), CONTEXT, START)
        self.assertEqual(details['大床房']['room_nights'], 30)
        self.assertEqual(details['大床房']['room_revenue'], 3000)
        self.assertEqual(excluded, 10)

    def test_rolling_thirty_day_summary_uses_cumulative_room_days(self):
        row = rows()[0]
        self.assertEqual((row['room_count'], row['room_nights'], row['occupancy_rate'], row['room_revenue'],
                          row['average_room_price'], row['revpar']), (60, 30, 50, 3000, 100, 50))
        self.assertEqual(row['section'], 'summary'); self.assertEqual(row['dimension_code'], '')
        self.assertIsNone(row['room_type_id']); self.assertIsNone(row['overnight_room_count'])

    def test_annual_ratio_rounding_only_accepts_whole_reported_room_days(self):
        value = report.cumulative_metrics(daily_cells(6417, '46.298701298701296', 100), CONTEXT)
        self.assertEqual(value['大床房']['room_days'], 13860)
        with self.assertRaises(ValueError): report.cumulative_metrics(daily_cells(10, 33), CONTEXT)
        with self.assertRaises(ValueError): report.cumulative_metrics(daily_cells(10, 0), CONTEXT)

    def test_unknown_capacity_is_null_and_never_invented_from_current_rooms(self):
        values = cumulative(); values[date(2026, 10, 5)]['大床房']['room_days'] = None
        details, _ = report.period_metrics(month_cells(), CONTEXT, START)
        row = report.build_rows(details, values, 'hotel', CONTEXT, START, END, NOW)[0]
        self.assertIsNone(row['room_count']); self.assertIsNone(row['occupancy_rate']); self.assertIsNone(row['revpar'])
        self.assertEqual(row['room_revenue'], 3000)
        self.assertIsNone(report.cumulative_metrics(daily_cells(0, 0, 0), CONTEXT)['大床房']['room_days'])

    def test_cross_year_resets_annual_baseline(self):
        today = date(2027, 1, 15); start, end = report.report_window(today)
        self.assertEqual(report.segments(start, end), [(date(2026, 12, 15), date(2026, 12, 31)), (None, date(2027, 1, 14))])
        values = {date(2026, 12, 15): {'大床房': dict(room_nights=D(15), room_revenue=D(1500), room_days=D(30))},
                  date(2026, 12, 31): {'大床房': dict(room_nights=D(31), room_revenue=D(3100), room_days=D(62))},
                  date(2027, 1, 14): {'大床房': dict(room_nights=D(14), room_revenue=D(1400), room_days=D(28))}}
        details, _ = report.period_metrics(month_cells(), CONTEXT, START)
        row = report.build_rows(details, values, 'hotel', CONTEXT, start, end, datetime(2027, 1, 15, 17))[0]
        self.assertEqual((row['room_count'], row['room_nights'], row['room_revenue']), (60, 30, 3000))
        self.assertEqual(report.segments(date(2026, 1, 1), date(2026, 1, 30)), [(None, date(2026, 1, 30))])

    def test_period_matches_cumulative_and_missing_room_is_not_silently_zeroed(self):
        details, _ = report.period_metrics(month_cells(), CONTEXT, START)
        for key, value in [('room_nights', 41), ('room_revenue', 4001), ('room_days', 20)]:
            values = cumulative(); values[END]['大床房'][key] = D(value)
            with self.subTest(key=key), self.assertRaises(ValueError):
                report.build_rows(details, values, 'hotel', CONTEXT, START, END, NOW)
        values = cumulative(); values[END] = {}
        with self.assertRaises(ValueError): report.build_rows(details, values, 'hotel', CONTEXT, START, END, NOW)

    def test_period_structure_total_and_metrics_must_match(self):
        for row, col, value in [(0, 1, '其他酒店'), (1, 4, 'NaN'), (3, 4, ''), (7, 17, 3011)]:
            data = month_cells(); data[row][col] = value
            with self.subTest(row=row, col=col), self.assertRaises(ValueError): report.period_metrics(data, CONTEXT, START)

    def test_envelope_must_match_requested_hotel_and_range(self):
        payload = {'ok': True, **CONTEXT, 'start_date': str(START), 'end_date': str(END),
                   'report_base64': base64.b64encode(b'workbook').decode()}
        book = MagicMock(); sheet = MagicMock(nrows=1); sheet.row_values.return_value = ['cell']; book.sheets.return_value = [sheet]
        with patch.object(report.xlrd, 'open_workbook', return_value=book):
            self.assertEqual(report.load_cells(payload, 'hotel', CONTEXT, START, END), [['cell']])
        book.release_resources.assert_called_once()
        for key in ('hotel_code', 'start_date', 'end_date'):
            with self.subTest(key=key), self.assertRaises(ValueError): report.load_cells({**payload, key: 'wrong'}, 'hotel', CONTEXT, START, END)

    def test_latest_snapshot_replacement_maps_exact_alias_and_preserves_other_source(self):
        conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[], [('大床房', 'mapped')]]
        storage.sync_mysql(rows(), 'hotel', NOW, conn)
        self.assertEqual(cur.execute.call_args_list[2].args[1], ('hotel', storage.SOURCE))
        self.assertEqual(cur.executemany.call_args.args[1][0]['room_type_id'], 'mapped')
        conn.commit.assert_called_once()

    def test_conflict_and_failed_insert_roll_back(self):
        for existing in [(storage.SOURCE, NOW.date(), datetime(2026, 11, 5, 18)), ('PMS（美团）', NOW.date(), NOW)]:
            conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value; cur.fetchall.return_value = [existing]
            with self.assertRaises(ValueError): storage.sync_mysql(rows(), 'hotel', NOW, conn)
            conn.rollback.assert_called_once(); cur.executemany.assert_not_called()
        conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[], []]; cur.executemany.side_effect = RuntimeError('write failed')
        with self.assertRaises(RuntimeError): storage.sync_mysql(rows(), 'hotel', NOW, conn)
        conn.rollback.assert_called_once(); conn.commit.assert_not_called()

    def test_daily_checkpoint_failure_skip_force_and_preview(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, \
                patch.dict('os.environ', {'HOTEL_ID': 'hotel', 'HOTEL_OTA_MYSQL_DATABASE': 'fake', 'HOTEL_OTA_OUTPUT_DIR': directory}), \
                patch.object(sys, 'argv', ['classification_report.py']), patch.object(report, 'now_shanghai', return_value=NOW), \
                patch.object(report, 'request', return_value=CONTEXT) as request, patch.object(report.time, 'sleep'), \
                patch.object(report, 'load_cells'), patch.object(report, 'period_metrics', return_value=({}, 0)), \
                patch.object(report, 'cumulative_metrics'), patch.object(report, 'build_rows', return_value=rows()), \
                patch.object(report, 'sync_mysql') as sync, patch('builtins.print'):
            sync.side_effect = RuntimeError('write failed')
            with self.assertRaises(RuntimeError): report.main()
            marker = Path(directory) / 'huiyun/jl11_checkpoints.json'; self.assertFalse(marker.exists())
            sync.side_effect = None; sync.reset_mock(); report.main()
            self.assertIn(f'booking|{START}|{END}|room_monthly', [call.args[0] for call in request.call_args_list])
            request.reset_mock(); report.main(); self.assertEqual(request.call_count, 1)
            with patch.object(sys, 'argv', ['classification_report.py', '--force']): report.main()
            previous = marker.read_bytes()
            with patch.object(sys, 'argv', ['classification_report.py', '--dry-run']): report.main()
            self.assertEqual(sync.call_count, 2); self.assertEqual(marker.read_bytes(), previous)

    def test_schema_uses_existing_summary_table(self):
        self.assertEqual(collection_schema.required_tables('pms_fetch', [('huiyun', ROOT / 'huiyun_pms/classification_report.py', [])]),
                         {storage.TABLE, 'hotel_room_type_mapping'})


if __name__ == '__main__': unittest.main()
