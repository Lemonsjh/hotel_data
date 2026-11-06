"""慧云房费分页汇总、原账户关联、退款及写入范围。"""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'OTA采集服务')]
from huiyun_pms import revenue_report as report
from huiyun_pms import revenue_storage as storage
import collection_schema

DAY, NOW = date(2026, 11, 4), datetime(2026, 11, 5, 16)
CONTEXT = {'hotel_code': 'native', 'hotel_name': '测试酒店'}


def fixture():
    account = {'acct_num': 'guest', 'htl_cd': 'native', 'resv_num': 'order', 'rm_num': '101', 'rm_typ': 'DL',
               'guest_name': '测试主客', 'customer_source': '渠道原名', 'rt_cd': 'OTA', 'rt_amt': 999,
               'chkin_dt': '2026-11-03 15:30:00', 'chko_dt': '1900-01-01 00:00:00'}
    transaction = {'trn_id': 1, 'trn_dt': '2026-11-04 00:00:00', 'trn_cd': '100', 'trn_drpt': '房费',
                   'trn_amt': '200', 'financenights': '1', 'rm_num': '101', 'acct_num': 'group',
                   'orgacct_num': 'guest', 'trn_oper': '夜审'}
    return {'ok': True, **CONTEXT, 'start_date': str(DAY), 'end_date': str(DAY),
            'total_amount': '230', 'total_nights': '1', 'room_types': {'DL': '大床', 'TW': '双床'},
            'rooms': {'101': 'DL', '102': 'TW'}, 'accounts': {'guest': account, 'group': {'guest_name': '团账名称'}},
            'transactions': [transaction, {**transaction, 'trn_id': 2, 'trn_amt': '-20', 'financenights': '0'},
                             {**transaction, 'trn_id': 3, 'trn_amt': '50', 'financenights': '0', 'rm_num': '102'}]}


def rows(payload=None):
    return report.transform(payload or fixture(), 'hotel', CONTEXT, DAY, DAY, NOW)


class HuiyunRevenueTests(unittest.TestCase):
    def test_original_guest_account_and_charged_room_drive_metadata(self):
        first, second = rows()
        self.assertEqual(first['guest_name'], '测试主客')
        self.assertEqual(first['customer_source'], '渠道原名')
        self.assertEqual(first['order_id'], 'order')
        self.assertEqual(second['room_type_name'], '双床')
        self.assertIsNone(first['checkout_time'])
        self.assertEqual(first['checkin_time'], datetime(2026, 11, 3, 15, 30))

    def test_refund_is_net_included_and_daily_price_uses_charged_amount(self):
        first, second = rows()
        self.assertEqual(first['room_fee'], Decimal('180.00'))
        self.assertEqual(first['room_nights'], Decimal('1.00'))
        self.assertEqual(first['room_daily_price'], Decimal('180.00'))
        self.assertIsNone(second['room_daily_price'])
        self.assertEqual(sum(row['room_fee'] for row in rows()), Decimal('230.00'))

    def test_invalid_duplicate_date_subject_and_total_are_rejected(self):
        for field, value in [('trn_id', 2), ('trn_dt', '2026-11-05'), ('trn_cd', '005')]:
            data = fixture(); data['transactions'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                rows(data)
        data = fixture(); data['total_amount'] = '231'
        with self.assertRaises(ValueError):
            rows(data)

    def test_missing_order_stays_empty_but_distinct_stays_cannot_be_collapsed(self):
        data = fixture(); data['accounts']['guest']['resv_num'] = None
        self.assertEqual(rows(data)[0]['order_id'], '')
        data['accounts']['other'] = {**data['accounts']['guest'], 'acct_num': 'other'}
        data['transactions'][1]['orgacct_num'] = 'other'
        with self.assertRaisesRegex(ValueError, '无法区分'):
            rows(data)

    def test_negative_nights_and_empty_days_remain_valid(self):
        data = fixture(); data['transactions'] = [{**data['transactions'][0], 'trn_amt': '-200', 'financenights': '-1'}]
        data.update(total_amount='-200', total_nights='-1')
        self.assertEqual(rows(data)[0]['room_nights'], Decimal('-1.00'))
        self.assertIsNone(rows(data)[0]['room_daily_price'])
        data.update(transactions=[], accounts={}, total_amount='0', total_nights='0')
        self.assertEqual(rows(data), [])

    def test_first_thirty_days_and_later_only_yesterday(self):
        self.assertEqual(report.collection_window(NOW.date(), False), (date(2026, 10, 6), DAY))
        self.assertEqual(report.collection_window(NOW.date(), True), (DAY, DAY))

    def test_storage_maps_aliases_and_cleans_only_own_scope(self):
        conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[], [('大床', 'unified')]]
        storage.sync_mysql(rows(), 'hotel', DAY, DAY, NOW, conn)
        conn.commit.assert_called_once()
        records = cur.executemany.call_args.args[1]
        self.assertEqual(records[0]['room_type_id'], 'unified')
        self.assertIsNone(records[1]['room_type_id'])
        self.assertEqual(cur.execute.call_args_list[2].args[1], ('hotel', storage.SOURCE, DAY, DAY, '房费'))
        self.assertEqual(cur.execute.call_args.args[1], ('hotel', storage.SOURCE, date(2025, 10, 2)))

    def test_other_pms_or_newer_snapshot_is_not_overwritten(self):
        for source, stamp in [('PMS（宝寓）', NOW), (storage.SOURCE, datetime(2026, 11, 5, 17))]:
            conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = [(source, stamp)]
            with self.subTest(source=source), self.assertRaises(ValueError):
                storage.sync_mysql(rows(), 'hotel', DAY, DAY, NOW, conn)
            conn.rollback.assert_called_once(); cur.executemany.assert_not_called()

    def test_failed_insert_rolls_back(self):
        conn = MagicMock(); cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [[], []]; cur.executemany.side_effect = RuntimeError('write failed')
        with self.assertRaises(RuntimeError):
            storage.sync_mysql(rows(), 'hotel', DAY, DAY, NOW, conn)
        conn.rollback.assert_called_once(); conn.commit.assert_not_called()

    def test_success_skips_same_day_force_uses_incremental_and_failure_does_not_mark(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory, \
                patch.dict('os.environ', {'HOTEL_ID': 'hotel', 'HOTEL_OTA_MYSQL_DATABASE': 'fake', 'HOTEL_OTA_OUTPUT_DIR': directory}), \
                patch.object(sys, 'argv', ['revenue_report.py']), patch.object(report, 'now_shanghai', return_value=NOW), \
                patch.object(report, 'request', return_value=CONTEXT) as request, \
                patch.object(report, 'transform', return_value=[]), patch.object(report, 'sync_mysql') as sync, patch('builtins.print'):
            sync.side_effect = RuntimeError('write failed')
            with self.assertRaises(RuntimeError): report.main()
            checkpoint = Path(directory) / 'huiyun/rs01_checkpoints.json'
            self.assertFalse(checkpoint.exists())
            sync.side_effect = None; sync.reset_mock(); report.main()
            self.assertEqual(sync.call_args.args[2:4], (date(2026, 10, 6), DAY))
            request.reset_mock(); report.main()
            self.assertEqual(request.call_count, 1); sync.assert_called_once()
            with patch.object(sys, 'argv', ['revenue_report.py', '--force']): report.main()
            self.assertEqual(sync.call_args.args[2:4], (DAY, DAY))
            before = checkpoint.read_bytes()
            with patch.object(sys, 'argv', ['revenue_report.py', '--dry-run']): report.main()
            self.assertEqual(checkpoint.read_bytes(), before)
            self.assertEqual(sync.call_count, 2)

    def test_entry_declares_fee_and_mapping_tables(self):
        tables = collection_schema.required_tables('pms_fetch', [('huiyun', ROOT / 'huiyun_pms/revenue_report.py', [])])
        self.assertEqual(tables, {storage.TABLE, 'hotel_room_type_mapping'})


if __name__ == '__main__':
    unittest.main()
