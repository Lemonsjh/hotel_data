"""历史报表每日成功标记、作用域隔离与空结果。"""
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from huiyun_pms.daily_checkpoint import DailyCheckpoint
from huiyun_pms import guest_report

NOW = datetime(2026, 11, 5, 16)


class HuiyunDailyCheckpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        env = patch.dict(os.environ, {'HOTEL_OTA_OUTPUT_DIR': temporary.name, 'HOTEL_ID': 'hotel',
                                     'HOTEL_OTA_MYSQL_HOST': 'db.test', 'HOTEL_OTA_MYSQL_PORT': '3306',
                                     'HOTEL_OTA_MYSQL_DATABASE': 'test_hotel'})
        env.start(); self.addCleanup(env.stop)

    def test_only_success_marks_today_and_the_next_day_is_due(self):
        checkpoint = DailyCheckpoint('jd02', 'hotel', 'code')
        self.assertFalse(checkpoint.initialized)
        self.assertFalse(checkpoint.done_today(NOW))
        self.assertFalse(checkpoint.path.exists())
        checkpoint.mark(NOW)
        loaded = DailyCheckpoint('jd02', 'hotel', 'code')
        self.assertTrue(loaded.initialized)
        self.assertTrue(loaded.done_today(NOW))
        self.assertFalse(loaded.done_today(NOW + timedelta(days=1)))

    def test_database_hotel_client_and_report_are_isolated(self):
        DailyCheckpoint('jy01', 'hotel', 'code').mark(NOW)
        for report, hotel, code in [('jy03', 'hotel', 'code'), ('jy01', 'other', 'code'), ('jy01', 'hotel', 'other')]:
            self.assertFalse(DailyCheckpoint(report, hotel, code).done_today(NOW))
        for name in ('HOTEL_OTA_MYSQL_HOST', 'HOTEL_OTA_MYSQL_PORT', 'HOTEL_OTA_MYSQL_DATABASE'):
            with patch.dict(os.environ, {name: 'other'}):
                self.assertFalse(DailyCheckpoint('jy01', 'hotel', 'code').done_today(NOW))

    def test_invalid_timestamp_does_not_skip_collection(self):
        checkpoint = DailyCheckpoint('jy01', 'hotel', 'code')
        checkpoint.values[checkpoint.key] = 'invalid'
        self.assertFalse(checkpoint.done_today(NOW))

    def test_guest_empty_day_commits_once_then_skips_and_can_be_forced(self):
        with patch.object(sys, 'argv', ['guest_report.py']), \
                patch.object(guest_report, 'datetime') as clock, \
                patch.object(guest_report, 'request', return_value={'hotel_code': 'code', 'room_types': {}}) as request, \
                patch.object(guest_report, 'transform', return_value=[]), \
                patch.object(guest_report, 'sync_guest_details') as sync, patch('builtins.print'):
            clock.now.return_value = NOW
            guest_report.main()
            self.assertEqual([call.args[0] for call in request.call_args_list], [
                'booking_context', 'booking|2026-11-04|2026-11-04|guests', 'booking_context'])
            self.assertTrue(DailyCheckpoint('jd02', 'hotel', 'code').done_today(NOW))
            request.reset_mock(); guest_report.main()
            self.assertEqual(request.call_count, 1)
            sync.assert_called_once()
            with patch.object(sys, 'argv', ['guest_report.py', '--force']):
                guest_report.main()
            self.assertEqual(sync.call_count, 2)

    def test_guest_database_failure_does_not_mark_the_day(self):
        with patch.object(sys, 'argv', ['guest_report.py']), \
                patch.object(guest_report, 'datetime') as clock, \
                patch.object(guest_report, 'request', return_value={'hotel_code': 'code', 'room_types': {}}), \
                patch.object(guest_report, 'transform', return_value=[]), \
                patch.object(guest_report, 'sync_guest_details', side_effect=RuntimeError('database failed')):
            clock.now.return_value = NOW
            with self.assertRaises(RuntimeError):
                guest_report.main()
        self.assertFalse(DailyCheckpoint('jd02', 'hotel', 'code').path.exists())


if __name__ == '__main__':
    unittest.main()
