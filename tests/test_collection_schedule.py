"""整点/半点/50分调度：不立即执行、不追赶错过时刻，等待中可修改。"""
from datetime import datetime
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'OTA采集服务')]
import collection_schedule as schedule
import manual_scheduler as scheduler
import settings_validation


class CollectionScheduleTests(unittest.TestCase):
    def test_next_selected_time_is_strictly_future(self):
        for now, minute, expected in [
            ('2026-11-06 10:20:00', 0, '2026-11-06 11:00:00'),
            ('2026-11-06 10:20:00', 30, '2026-11-06 10:30:00'),
            ('2026-11-06 10:20:00', 50, '2026-11-06 10:50:00'),
            ('2026-11-06 10:50:00', 50, '2026-11-06 11:50:00'),
            ('2026-12-31 23:59:59', 0, '2027-01-01 00:00:00')]:
            with self.subTest(now=now, minute=minute):
                self.assertEqual(schedule.next_collection_time(datetime.fromisoformat(now), minute), datetime.fromisoformat(expected))

    def test_legacy_settings_default_to_hourly_and_invalid_new_values_are_rejected(self):
        self.assertEqual(schedule.collection_minute({'service': {'interval_minutes': 30}}), 0)
        for value in (0, 30, 50):
            self.assertEqual(settings_validation.validate_settings({'service': {'collection_minute': value}}), [])
        for value in (-1, 15, 60, '30', True, 30.5):
            with self.subTest(value=value):
                self.assertTrue(settings_validation.validate_settings({'service': {'collection_minute': value}}))
                with self.assertRaises(ValueError): schedule.next_collection_time(datetime(2026, 11, 6), value)

    def test_configuration_uses_same_field_position_and_saves_integer(self):
        import panel
        settings = {'service': {'interval_minutes': 30}}
        html = panel.config_control(settings, 'service.collection_minute', '每小时采集时刻', False)
        self.assertIn("<select id='collection-minute'", html)
        self.assertIn("value='0' selected", html)
        with panel.app.test_request_context('/config', method='POST', data={'service.collection_minute': '50'}):
            result = panel.apply_form_to_settings(settings)
        self.assertEqual(result['service']['collection_minute'], 50)
        self.assertEqual(result['service']['interval_minutes'], 30)

    def test_busy_detection_does_not_block_forever_on_unowned_old_status(self):
        import runner
        for status, busy in [({'last_run_status': 'running'}, False),
                             ({'last_run_status': 'running', 'run_process_pid': 123}, True),
                             ({'last_run_status': 'failed'}, False)]:
            with self.subTest(status=status), patch.object(runner, 'reconcile_stale_run', return_value=status):
                self.assertEqual(scheduler.collection_busy(), busy)

    def test_real_process_collects_once_immediately_then_waits(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory).resolve()
            self.assertTrue(root.is_relative_to(ROOT))
            (root / 'settings.json').write_text(json.dumps({'service': {'scheduler_enabled': True}}))
            marker = root / 'collected.txt'
            (root / 'runner.py').write_text(f'from pathlib import Path\nwith Path({str(marker)!r}).open("a") as out: out.write("collected\\n")')
            code = f"""
import sys, subprocess
from pathlib import Path
from datetime import datetime
sys.path.insert(0, {str(ROOT / 'OTA采集服务')!r})
import manual_scheduler as s
root = Path({str(root)!r})
s.CONFIG_PATH = root / 'settings.json'
s.RUNNER_PATH = root / 'runner.py'
s.STATE_DIR = root
s.STATUS_PATH = root / 'status.json'
s.PID_PATH = root / 'pid'
s.STOP_PATH = root / 'stop'
s.RUN_STOP_PATH = root / 'run.stop'
s.LOG_PATH = root / 'logs/scheduler.log'
s.now_shanghai = lambda: datetime(2026, 11, 6, 10, 20)
s.collection_busy = lambda: False
s.run_collection = lambda: subprocess.run([sys.executable, str(s.RUNNER_PATH)], cwd=root).returncode
s.run_scheduler()
"""
            process = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', code], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    if (root / 'status.json').exists():
                        status = json.loads((root / 'status.json').read_text())
                        if status.get('scheduler_status') == 'waiting': break
                    time.sleep(0.05)
                else:
                    self.fail('isolated scheduler did not enter waiting state')
                self.assertEqual(status['next_run_at'], '2026-11-06 11:00:00')
                self.assertEqual(marker.read_text(), 'collected\n')
                (root / 'stop').touch()
                _, error = process.communicate(timeout=6)
                self.assertEqual(process.returncode, 0, error.decode('utf-8', errors='replace'))
                self.assertEqual(marker.read_text(), 'collected\n')
            finally:
                if process.poll() is None:
                    process.terminate(); process.communicate(timeout=5)


class ManualHourlySchedulerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, filename in [('CONFIG_PATH', 'settings.json'), ('RUNNER_PATH', 'runner.py'),
                               ('STATE_DIR', 'state'), ('STATUS_PATH', 'state/status.json'),
                               ('PID_PATH', 'state/pid'), ('STOP_PATH', 'state/stop')]:
            self.enterContext(patch.object(scheduler, name, root / filename))
        scheduler.RUNNER_PATH.write_text('', encoding='utf-8')
        self.settings = {'service': {'scheduler_enabled': True, 'collection_minute': 0}}
        self.write_settings()
        self.now = datetime(2026, 11, 6, 10, 20)
        self.enterContext(patch.object(scheduler, 'now_shanghai', side_effect=lambda: self.now))
        self.enterContext(patch.object(scheduler, 'collection_busy', return_value=False))

    def write_settings(self):
        scheduler.CONFIG_PATH.write_text(json.dumps(self.settings), encoding='utf-8')

    def test_immediate_first_run_then_skip_missed_hours(self):
        waits = []
        def wait(target):
            waits.append(target)
            self.now = target
            return True
        def collect():
            self.now = datetime(2026, 11, 6, 13, 5)
            return 1  # 失败也不立即连续补跑。
        with patch.object(scheduler, 'wait_for_next_run', side_effect=wait), \
                patch.object(scheduler, 'run_collection', side_effect=collect) as run:
            scheduler.run_scheduler()
        self.assertEqual(waits, [datetime(2026, 11, 6, 14)])
        run.assert_called_once()
        self.assertFalse(scheduler.PID_PATH.exists())
        self.assertEqual(json.loads(scheduler.STATUS_PATH.read_text())['last_run_return_code'], 1)

    def test_stop_while_waiting_never_starts_another_collection(self):
        with patch.object(scheduler, 'wait_for_next_run', return_value=True), patch.object(scheduler, 'run_collection', return_value=0) as run:
            scheduler.run_scheduler()
        run.assert_called_once()

    def test_busy_manual_collection_skips_this_slot(self):
        waits = []
        def wait(target):
            waits.append(target); self.now = target
            return len(waits) > 1
        with patch.object(scheduler, 'wait_for_next_run', side_effect=wait), \
                patch.object(scheduler, 'collection_busy', return_value=True), patch.object(scheduler, 'run_collection') as run:
            scheduler.run_scheduler()
        run.assert_not_called()
        self.assertEqual(waits, [datetime(2026, 11, 6, 11), datetime(2026, 11, 6, 12)])

    def test_waiting_schedule_change_retargets_without_immediate_run(self):
        sleeps = []
        def sleep(_):
            sleeps.append(1)
            if len(sleeps) == 1:
                self.now = datetime(2026, 11, 6, 10, 25)
                self.settings['service']['collection_minute'] = 30
                self.write_settings()
            else:
                self.now = datetime(2026, 11, 6, 10, 30)
        with patch.object(scheduler.time, 'sleep', side_effect=sleep):
            self.assertFalse(scheduler.wait_for_next_run(datetime(2026, 11, 6, 11)))
        status = json.loads(scheduler.STATUS_PATH.read_text())
        self.assertEqual(status['next_run_at'], '2026-11-06 10:30:00')
        self.assertEqual(len(sleeps), 2)

    def test_disabling_configuration_stops_waiting(self):
        self.settings['service']['scheduler_enabled'] = False; self.write_settings()
        with patch.object(scheduler.time, 'sleep') as sleep:
            self.assertTrue(scheduler.wait_for_next_run(datetime(2026, 11, 6, 11)))
        sleep.assert_not_called()


if __name__ == '__main__': unittest.main()
