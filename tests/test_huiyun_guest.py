"""慧云JD02逐客保存、真实离店时间和失败保护。"""
from datetime import date, datetime
from decimal import Decimal
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'OTA采集服务')]
from huiyun_pms import guest_report
import collection_schema
from OTA采集服务.pms_guest_storage import sync_guest_details


def fixture():
    return {'ok': True, 'hotel_code': '255201', 'hotel_name': '测试酒店', 'departure_date': '2026-11-04',
            'room_types': {'VT': '双床房'}, 'profiles': {
                'guest1': {'name': '测试主客', 'id_card': 'MASKED1'},
                'guest2': {'name': '测试同住人', 'id_card': 'MASKED2'}}, 'records': [
                {'acct_num': 'account1', 'gh_num': 'guest1', 'resv_num': 'same-order', 'rm_typ': 'VT',
                 'rm_num': '101', 'rt_amt': '240', 'arr_dt': '2026-11-02 00:00:00', 'dpt_dt': '2026-11-05 00:00:00',
                 'chkin_dt': '2026-11-03 20:26:00', 'chko_dt': '2026-11-04 08:43:00', 'AcctStus': '已结'},
                {'acct_num': 'account2', 'gh_num': 'guest2', 'resv_num': 'same-order', 'rm_typ': 'VT',
                 'rm_num': '101', 'rt_amt': '0', 'chkin_dt': '2026-11-03 20:26:00',
                 'chko_dt': '2026-11-04 08:43:00', 'AcctStus': '已结'}]}


class HuiyunGuestTests(unittest.TestCase):
    def setUp(self):
        self.day, self.now = date(2026, 11, 4), datetime(2026, 11, 5, 12)

    def rows(self, data=None):
        return guest_report.transform(data or fixture(), 'hotel', self.day, self.now)

    def test_same_order_preserves_both_guests_and_original_prices(self):
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['order_id'], rows[1]['order_id'])
        self.assertEqual(rows[0]['room_price'], Decimal('240'))
        self.assertEqual(rows[1]['room_price'], Decimal('0'))
        self.assertEqual({r['customer_id'] for r in rows}, {'guest1', 'guest2'})
        self.assertIsNone(rows[0]['main_checkin_person'])

    def test_actual_times_are_used_instead_of_planned_dates(self):
        row = self.rows()[0]
        self.assertEqual(row['arrival_time'], datetime(2026, 11, 3, 20, 26))
        self.assertEqual(row['departure_time'], datetime(2026, 11, 4, 8, 43))
        self.assertEqual(row['business_date'], self.day)

    def test_order_source_preserves_original_channel_or_company_name(self):
        for name, platform in [('上海携程国际旅行社有限公司', '携程'), ('天津西瓜旅游有限责任公司（美团）', '美团'),
                               ('美团', '美团'), ('协议单位', None), ('美团/携程', None)]:
            data = fixture(); data['records'][0]['TaNum'] = f' {name} '
            rows = self.rows(data)
            with self.subTest(name=name):
                self.assertEqual(rows[0]['order_source'], name)
                self.assertEqual(rows[0]['guest_source'], platform)
                self.assertIsNone(rows[1]['order_source'])
                self.assertIsNone(rows[1]['guest_source'])

    def test_unknown_source_stays_null_without_guessing_from_account_type(self):
        for value in (None, '', '  '):
            data = fixture(); data['records'][0].update(TaNum=value, acct_typ='06')
            with self.subTest(value=value):
                self.assertIsNone(self.rows(data)[0]['order_source'])
                self.assertIsNone(self.rows(data)[0]['guest_source'])

    def test_invalid_departure_and_cancelled_unchecked_in_record_fail(self):
        for field, value in [('chko_dt', '2026-11-05 08:43:00'), ('chkin_dt', '1900-01-01 00:00:00')]:
            data = fixture(); data['records'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.rows(data)

    def test_in_house_and_cancelled_accounts_with_stale_departure_are_excluded(self):
        data = fixture()
        data['records'][0].update(AcctStus='在店', chkin_dt='2026-11-04 10:00:00', chko_dt='2026-11-04 08:00:00')
        data['records'][1]['AcctStus'] = '预订取消'
        self.assertEqual(self.rows(data), [])

    def test_missing_profile_fails_and_missing_order_stays_null(self):
        data = fixture(); del data['profiles']['guest1']
        with self.assertRaises(ValueError):
            self.rows(data)
        data = fixture(); data['records'][0]['resv_num'] = None
        self.assertIsNone(self.rows(data)[0]['order_id'])

    def test_repeated_account_guest_is_deduplicated_and_conflict_fails(self):
        data = fixture(); data['records'].append(copy.deepcopy(data['records'][0]))
        self.assertEqual(len(self.rows(data)), 2)
        data['records'][-1]['rt_amt'] = 250
        with self.assertRaisesRegex(ValueError, '冲突'):
            self.rows(data)

    def test_empty_day_is_valid(self):
        data = fixture(); data.update(records=[], profiles={})
        self.assertEqual(self.rows(data), [])

    def test_shared_storage_maps_only_huiyun_and_retains_sixty_days(self):
        conn = MagicMock(); cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [('双床房', 'unified')]
        sync_guest_details(self.rows(), 'hotel', self.now, guest_report.SOURCE, 'pms_huiyun', conn)
        conn.commit.assert_called_once()
        self.assertEqual(cursor.execute.call_args_list[0].args[1], ('hotel', 'pms_huiyun'))
        self.assertEqual(len(cursor.executemany.call_args.args[1]), 2)
        self.assertEqual(cursor.execute.call_args.args[1], ('hotel', 'PMS（慧云）', date(2026, 9, 6)))

    def test_guest_failure_never_calls_database_writer(self):
        with patch.object(sys, 'argv', ['guest_report.py']), patch.dict('os.environ', {'HOTEL_ID': 'hotel'}), \
             patch.object(guest_report, 'request', side_effect=RuntimeError('query failed')), \
             patch.object(guest_report, 'sync_guest_details') as sync:
            with self.assertRaises(RuntimeError):
                guest_report.main()
        sync.assert_not_called()

    def test_guest_entry_requires_only_guest_and_mapping_tables(self):
        tables = collection_schema.required_tables('pms_fetch', [('huiyun', ROOT / 'huiyun_pms/guest_report.py', [])])
        self.assertEqual(tables, {'jd02_guest_stay_detail', 'hotel_room_type_mapping'})


if __name__ == '__main__':
    unittest.main()
