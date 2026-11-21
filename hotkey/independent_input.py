"""独立键盘活动观察器：不依赖 keyboard 钩子，用于热键自检兜底。

keyboard 库的底层钩子被系统摘除、或钩子线程仍存活但收不到事件时，
现有 input-stale 判据依赖同一个钩子刷新时间戳，容易漏报。这里用 Win32
GetAsyncKeyState 在独立线程轮询物理键盘状态；只记录“最近一次键盘活动”，
不派发热键、不调用 keyboard 的任何 API，也不记录按键内容。
"""
from __future__ import annotations

import ctypes
import os
import threading
import time


_MOUSE_VKS = frozenset(range(0x01, 0x08))


def _windows_down_vks():
    """返回当前按下的非鼠标虚拟键集合；非 Windows 返回 None。"""
    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32
        down = set()
        for vk in range(0x08, 0xFF):
            if vk in _MOUSE_VKS:
                continue
            if user32.GetAsyncKeyState(vk) & 0x8000:
                down.add(vk)
        return down
    except (AttributeError, OSError):
        return None


class IndependentKeyboardObserver:
    """后台轮询键盘状态，只提供独立的活动时间。

    可注入 `backend` 供测试返回当前按下的虚拟键集合；生产默认使用
    GetAsyncKeyState。线程不碰 Qt，不持有窗口。
    """

    def __init__(self, poll_interval=0.02, backend=None):
        self._poll_interval = max(0.005, float(poll_interval))
        self._backend = backend or _windows_down_vks
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_event_at = None
        self.available = False
        self.error = None

    @property
    def last_event_at(self):
        with self._lock:
            return self._last_event_at

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="independent-keyboard-input", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.5)
        self._thread = None
        self.available = False

    def _run(self):
        while not self._stop.is_set():
            try:
                pressed = self._backend()
            except Exception as error:  # noqa: BLE001 观察器失败不能影响主进程
                self.error = type(error).__name__
                self.available = False
                return
            if pressed is None:
                self.available = False
                return
            keyboard_pressed = set(pressed) - _MOUSE_VKS
            if keyboard_pressed:
                with self._lock:
                    self._last_event_at = time.monotonic()
            self.available = True
            self._stop.wait(self._poll_interval)
