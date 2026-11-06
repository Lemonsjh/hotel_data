"""房态口径、账号范围及预览不写库的离线回归。"""
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'OTA采集服务')]
from huiyun_pms.room_status import transform
from huiyun_pms import collector


def fixture():
    def room(number, state, room_type='VT', reserved='0', clean='0'):
        return {'rm_num': number, 'htl_cd': 'TEST', 'rm_typ': room_type, 'bld_cd': 'A',
                'rm_stus': state, 'todayresvflg': reserved, 'cln_stus': clean, 'except_flg': '1'}
    return {'ok': True, 'hotel_code': 'TEST', 'hotel_name': '测试酒店', 'business_date': '2026-11-03',
            'captured_at': '2026-11-04T04:25:00', 'reports': {
                'GetRoomInfoDataCompress': [{'name': 'roomstus', 'records': [
                    room('101', 'O'), room('102', 'V', reserved='1'), room('103', 'V', clean='1'),
                    room('104', 'A'), room('105', 'U'), room('106', 'S'), room('999', 'O', '**')]}],
                'GetRoomtypUsing': [{'name': 'sysconf', 'records': [
                    {'para_cd': 'VT ', 'para_drpt': '标准房 '}, {'para_cd': '**', 'para_drpt': '哑房'}]}],
                'GetRoomUsedInfoObj': [{'name': 'room_revenue', 'records': [
                    {'room_type_id': 'VT', 'stay_date': '2026-11-03', 'total': 6, 'inHouse': 1,
                     'definiteResv': 1, 'ResvAmt': 128, 'tmpAmt': 8}]}]}}


class RoomStatusTests(unittest.TestCase):
    def test_real_rooms_and_allocations_are_not_confused_with_maintenance(self):
        result = transform(fixture())
        summary = result['summary']
        self.assertEqual(result['excluded_dummy_rooms'], 1)
        self.assertEqual(summary['total_rooms'], 6)
        self.assertEqual(summary['occupied_rooms'], 2)
        self.assertEqual(summary['in_house_rooms'], 1)
        self.assertEqual(summary['reserved_rooms'], 1)
        self.assertEqual(summary['available_rooms'], 1)
        self.assertEqual(summary['temporary_allocated_rooms'], 1)
        self.assertEqual(summary['maintenance_rooms'], 1)
        self.assertEqual(summary['out_of_service_rooms'], 1)
        self.assertEqual(result['room_types'][0]['room_type_name'], '标准房')
        self.assertIsNone(result['room_types'][0]['overbooking_rooms'])
        self.assertIsNone(result['room_types'][0]['hotel_id'])

    def test_real_revenue_excludes_temporary_reservations_and_computes_rates(self):
        result = transform(fixture())
        row = result['room_types'][0]
        self.assertEqual(row['room_revenue'], '120')
        self.assertEqual(row['adr'], '60.0000')
        self.assertEqual(row['revpar'], '20.0000')
        self.assertEqual(result['summary']['room_revenue'], '120')

    def test_missing_money_wrong_date_or_mismatched_counts_fail(self):
        for field, value in [('ResvAmt', None), ('tmpAmt', 'NaN'), ('stay_date', '2026-11-04'),
                             ('total', 7), ('definiteResv', 2), ('room_type_id', 'OTHER')]:
            payload = fixture()
            payload['reports']['GetRoomUsedInfoObj'][0]['records'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                transform(payload)
        payload = fixture()
        del payload['reports']['GetRoomUsedInfoObj']
        with self.assertRaises(ValueError):
            transform(payload)

    def test_empty_room_type_has_zero_rates_but_nonzero_revenue_is_rejected(self):
        payload = fixture()
        for room in payload['reports']['GetRoomInfoDataCompress'][0]['records'][:2]:
            room.update(rm_stus='V', todayresvflg='0')
        record = payload['reports']['GetRoomUsedInfoObj'][0]['records'][0]
        record.update(inHouse=0, definiteResv=0, ResvAmt=0, tmpAmt=0)
        row = transform(payload)['room_types'][0]
        self.assertEqual(row['adr'], '0.0000')
        self.assertEqual(row['revpar'], '0.0000')
        record['ResvAmt'] = 1
        with self.assertRaises(ValueError):
            transform(payload)

    def test_nullable_no_reservation_marker_and_real_collection_hour(self):
        payload = fixture()
        payload['reports']['GetRoomInfoDataCompress'][0]['records'][2]['todayresvflg'] = None
        row = transform(payload)['room_types'][0]
        self.assertEqual(row['available_rooms'], 1)
        self.assertEqual(row['snapshot_hour'], '2026-11-04T04:00:00+08:00')
        self.assertEqual(row['stay_date'], '2026-11-03')

    def test_duplicate_room_never_inflates_inventory_and_conflicts_fail(self):
        payload = fixture()
        rooms = payload['reports']['GetRoomInfoDataCompress'][0]['records']
        rooms.append(copy.deepcopy(rooms[0]))
        self.assertEqual(transform(payload)['summary']['total_rooms'], 6)
        rooms[-1]['rm_stus'] = 'V'
        with self.assertRaisesRegex(ValueError, '冲突'):
            transform(payload)

    def test_wrong_hotel_unknown_type_and_unknown_state_fail(self):
        for field, value in [('htl_cd', 'OTHER'), ('rm_typ', 'MISSING'), ('rm_stus', 'UNKNOWN')]:
            payload = fixture()
            payload['reports']['GetRoomInfoDataCompress'][0]['records'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                transform(payload)

    def test_missing_reservation_column_or_empty_data_never_become_zero_inventory(self):
        payload = fixture()
        del payload['reports']['GetRoomInfoDataCompress'][0]['records'][2]['todayresvflg']
        with self.assertRaises(ValueError):
            transform(payload)
        payload['reports']['GetRoomInfoDataCompress'][0]['records'] = []
        with self.assertRaises(ValueError):
            transform(payload)

    def test_collector_writes_preview_only_and_failed_capture_preserves_previous_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            args = ['collector.py', '--dry-run', '--output-dir', folder]
            with patch.object(sys, 'argv', args), patch.object(collector, 'request', return_value=fixture()), \
                 patch('mysql_connection.connect_mysql') as connect:
                self.assertEqual(collector.main(), 0)
            connect.assert_not_called()
            path = Path(folder) / 'huiyun_room_status_preview.json'
            before = path.read_bytes()
            self.assertFalse(json.loads(before)['database_written'])
            with patch.object(sys, 'argv', args), patch.object(collector, 'request', side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError):
                    collector.main()
            self.assertEqual(path.read_bytes(), before)
            invalid = fixture()
            del invalid['reports']['GetRoomUsedInfoObj']
            with patch.object(sys, 'argv', args), patch.object(collector, 'request', return_value=invalid):
                with self.assertRaises(ValueError):
                    collector.main()
            self.assertEqual(path.read_bytes(), before)

    def test_release_includes_sources_but_excludes_runtime_binaries(self):
        sys.path.insert(0, str(ROOT / 'updater'))
        package = importlib.import_module('package_format')
        for file in (ROOT / 'huiyun_pms').glob('*'):
            if file.is_file():
                package.allowed_path(file.relative_to(ROOT).as_posix())
        with self.assertRaises(ValueError):
            package.allowed_path('huiyun_pms/ClientHost.exe')


if __name__ == '__main__':
    unittest.main()
