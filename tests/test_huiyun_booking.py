"""慧云 JD01 房间去重、日期范围和事务保护。"""
from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from huiyun_pms import booking_report, booking_storage, booking_transform


def account(account_id, room, price, status='已结', order='booking'):
    return {'acct_num': account_id, 'resv_num': order, 'rm_typ': 'VT', 'rm_num': room,
            'rt_amt': price, 'arr_dt': '2026-11-05 00:00:00', 'dpt_dt': '2026-11-06 00:00:00',
            'rm_nums': 1, 'AcctStus': status, 'shr_num': 'room-share', 'rt_cd': 'code'}


def payload(records):
    return {'ok': True, 'hotel_code': '255201', 'hotel_name': '测试酒店', 'start_date': '2026-10-07',
            'end_date': '2026-11-05', 'room_types': {'VT': '景观双床房'}, 'records': records}


class HuiyunBookingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 5, 12)
        self.start, self.end = date(2026, 10, 7), date(2026, 11, 5)

    def rows(self, records):
        return booking_transform.transform(payload(records), 'hotel', self.now, self.start, self.end)

    def test_shared_guest_zero_price_does_not_create_extra_room(self):
        rows = self.rows([account('1', '8316', '240'), account('2', '8316', '0'),
                          account('3', '8317', '240', '预订取消')])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['room_count'], 2)
        self.assertEqual(rows[0]['room_price'], Decimal('240'))
        self.assertEqual(set(rows[0]['booking_status'].split('、')), {'已结', '预订取消'})

    def test_different_prices_and_stays_are_preserved(self):
        extra = account('3', '8318', '240')
        extra['dpt_dt'] = '2026-11-07 00:00:00'
        rows = self.rows([account('1', '8316', '240'), account('2', '8317', '300'), extra])
        self.assertEqual(len(rows), 3)

    def test_ambiguous_room_price_and_duplicate_account_fail(self):
        with self.assertRaisesRegex(ValueError, '多个非零房价'):
            self.rows([account('1', '8316', '240'), account('2', '8316', '300')])
        with self.assertRaisesRegex(ValueError, '冲突记录'):
            self.rows([account('1', '8316', '240'), account('1', '8317', '240')])

    def test_empty_result_is_valid_and_invalid_scope_fails(self):
        self.assertEqual(self.rows([]), [])
        item = account('1', '8316', '240')
        item['arr_dt'] = '2026-10-06 00:00:00'
        with self.assertRaises(ValueError):
            self.rows([item])

    def test_team_master_without_room_type_is_not_a_room(self):
        master = account('master', '', 0, '团队')
        master['rm_typ'] = ''
        rows = self.rows([master, account('1', '8316', '240')])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['room_count'], 1)

    def test_database_replaces_only_date_and_source_with_90_day_retention(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[], [('景观双床房', 'mapped')]]
        booking_storage.sync_mysql(self.rows([account('1', '8316', '240')]), 'hotel', self.start, self.end, self.now, conn)
        conn.commit.assert_called_once()
        inserts = cursor.executemany.call_args.args[1]
        self.assertEqual(inserts[0]['room_type_id'], 'mapped')
        deletes = [c for c in cursor.execute.call_args_list if c.args[0].startswith('DELETE')]
        self.assertEqual(len(deletes), 2)
        self.assertTrue(all('source_platform=%s' in c.args[0] for c in deletes))
        self.assertEqual(deletes[-1].args[1][-1], datetime(2026, 8, 8))

    def test_storage_rolls_back_on_insert_failure(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[], []]
        cursor.executemany.side_effect = RuntimeError('failed')
        with self.assertRaises(RuntimeError):
            booking_storage.sync_mysql(self.rows([account('1', '8316', '240')]), 'hotel', self.start, self.end, self.now, conn)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_empty_first_success_marks_initialized_and_next_queries_today(self):
        class Clock:
            @classmethod
            def now(cls, zone):
                return datetime(2026, 11, 5, 12)
        calls = []
        def request(command, **kwargs):
            calls.append(command)
            if command == 'booking_context':
                return {'hotel_code': '255201'}
            begin, end = command.split('|')[1:]
            return {**payload([]), 'start_date': begin, 'end_date': end}
        with tempfile.TemporaryDirectory(dir=ROOT) as folder, patch.dict(os.environ, {
                'HOTEL_ID': 'hotel', 'HOTEL_OTA_MYSQL_DATABASE': 'database', 'HOTEL_OTA_OUTPUT_DIR': folder}), \
             patch.object(sys, 'argv', ['booking_report.py']), patch.object(booking_report, 'datetime', Clock), \
             patch.object(booking_report, 'request', side_effect=request), patch.object(booking_report, 'sync_mysql') as sync:
            booking_report.main()
            booking_report.main()
            self.assertEqual(calls, ['booking_context', 'booking|2026-10-07|2026-11-05',
                                     'booking_context', 'booking|2026-11-05|2026-11-05'])
            self.assertEqual(sync.call_count, 2)
            self.assertTrue(json.loads((Path(folder) / 'huiyun/jd01_checkpoints.json').read_text(encoding='utf-8')))

    def test_failed_database_write_does_not_mark_initial_backfill_complete(self):
        class Clock:
            @classmethod
            def now(cls, zone):
                return datetime(2026, 11, 5, 12)
        def request(command, **kwargs):
            return {'hotel_code': '255201'} if command == 'booking_context' else payload([])
        with tempfile.TemporaryDirectory(dir=ROOT) as folder, patch.dict(os.environ, {
                'HOTEL_ID': 'hotel', 'HOTEL_OTA_MYSQL_DATABASE': 'database', 'HOTEL_OTA_OUTPUT_DIR': folder}), \
             patch.object(sys, 'argv', ['booking_report.py']), patch.object(booking_report, 'datetime', Clock), \
             patch.object(booking_report, 'request', side_effect=request), \
             patch.object(booking_report, 'sync_mysql', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                booking_report.main()
            self.assertFalse((Path(folder) / 'huiyun/jd01_checkpoints.json').exists())


if __name__ == '__main__':
    unittest.main()
