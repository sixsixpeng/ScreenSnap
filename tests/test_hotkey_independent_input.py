"""独立键盘观察器：不依赖真实桌面，使用可注入 backend。"""
import os
import sys
import time
from pathlib import Path
import unittest

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)

from hotkey.independent_input import IndependentKeyboardObserver


class IndependentKeyboardObserverTests(unittest.TestCase):
    def test_activity_updates_only_for_keyboard_keys(self):
        pressed = {0x01}  # 左键是鼠标，不算键盘活动
        observer = IndependentKeyboardObserver(poll_interval=0.01, backend=lambda: set(pressed))
        try:
            observer.start()
            deadline = time.monotonic() + 1
            while observer.last_event_at is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(observer.last_event_at)
            pressed.add(0x41)
            deadline = time.monotonic() + 1
            while observer.last_event_at is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(observer.last_event_at)
        finally:
            observer.stop()

    def test_unavailable_backend_marks_observer_unavailable(self):
        observer = IndependentKeyboardObserver(poll_interval=0.01, backend=lambda: None)
        try:
            observer.start()
            deadline = time.monotonic() + 1
            while observer.available and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(observer.available)
        finally:
            observer.stop()


if __name__ == "__main__":
    unittest.main()