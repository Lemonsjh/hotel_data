import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '美团OTA数据采集代码'))
import meituan_page_capture as capture


class BrowserLockTests(unittest.TestCase):
    def test_current_process_is_alive_without_terminating_it(self):
        self.assertTrue(capture.process_alive(os.getpid()))
        self.assertFalse(capture.process_alive(0))

    def test_locked_stale_file_waits_until_handle_released(self):
        path = MagicMock()
        lock = path.__truediv__.return_value
        lock.read_text.return_value = '99999'
        lock.unlink.side_effect = [PermissionError('file busy'), None, None]
        with patch.object(capture, 'profile_path', return_value=path), \
             patch.object(capture, 'process_alive', return_value=False), \
             patch.object(capture.os, 'open', side_effect=[FileExistsError(), FileExistsError(), 42]), \
             patch.object(capture.os, 'write'), patch.object(capture.os, 'close'), \
             patch.object(capture.time, 'sleep') as sleep:
            with capture.browser_profile_lock(timeout_seconds=10):
                pass
        sleep.assert_called_once_with(1)
        self.assertEqual(lock.unlink.call_count, 3)

    def test_incomplete_lock_is_not_deleted(self):
        path = MagicMock()
        lock = path.__truediv__.return_value
        lock.read_text.return_value = ''
        with patch.object(capture, 'profile_path', return_value=path), \
             patch.object(capture.os, 'open', side_effect=FileExistsError()), \
             patch.object(capture.time, 'monotonic', side_effect=[0, 2]), \
             patch.object(capture.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'profile is busy'):
                with capture.browser_profile_lock(timeout_seconds=1):
                    pass
        lock.unlink.assert_not_called()


if __name__ == '__main__':
    unittest.main()
