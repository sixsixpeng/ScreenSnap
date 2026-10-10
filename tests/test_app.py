"""应用层：启动、单实例、异常钩子、托盘与通知。"""

import os
import sys

# 无论 -m unittest、目录内 discover 还是直接跑文件，都要能导入 tests.base。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tests.base import (CoreTests, Mock, patch, Path, tempfile, json, unittest, Image,
                        QPointF, Qt, QPoint, QRect, QRectF, QTest, QApplication,
                        ConfigManager, resolved_dir, AnnotationCanvas, shape,
                        EditorWindow, SelectionRects, StickerItem, StickerManager,
                        SettingsWindow, HotkeyEdit)


class AppTests(CoreTests):
    def test_notification_identity_registration_is_idempotent(self):
        """回归（2026-10-11 用户反馈「Windows 通知设置里没有独立的 ScreenSnap 入口」）：
        Windows 只在系统里存在「带 ScreenSnap AUMID 的开始菜单快捷方式」时，才允许原生通知使用
        该身份，并在「设置 → 系统 → 通知」里列出独立条目。验证 ① 首次注册写出 AUMID、
        ② 重复调用幂等（不重建）、③ 身份不对时重建、④ 注册失败不抛异常（通知照旧回退）。"""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from core import app_identity

        with tempfile.TemporaryDirectory() as folder:
            link = Path(folder) / "ScreenSnap.lnk"
            self.assertEqual(app_identity.ensure_registered(link), "created")
            self.assertEqual(app_identity.read_app_id(link), app_identity.APP_USER_MODEL_ID,
                             "注册后 .lnk 必须带上 ScreenSnap 的 AppUserModelID")
            self.assertEqual(app_identity.ensure_registered(link), "exists",
                             "重复调用必须幂等（不重建快捷方式）")
            link.write_bytes(b"broken link")
            self.assertEqual(app_identity.ensure_registered(link), "created",
                             "已存在但身份不对时必须重建")
        elsewhere = Path(tempfile.mkdtemp()) / "ScreenSnap.lnk"
        with patch.object(app_identity, "_create_shortcut", side_effect=OSError("no shell")):
            self.assertEqual(app_identity.ensure_registered(elsewhere), "unavailable",
                             "注册失败只返回状态，不能抛异常")

    def test_notification_switches_gate_only_matching_messages(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application
        app = Application.__new__(Application)
        app.config = SimpleNamespace(data=DEFAULTS.copy())
        app.config.data["notification_backend"] = "legacy"
        app.tray = Mock()
        app.logger = Mock()
        app.stickers = Mock()
        app.settings_window = Mock()
        app.editors = []
        app.capture_notice = None
        with patch("main.QApplication.beep"), patch("app.notification_flow.CaptureNotification") as preview, \
            patch("main.QTimer"):
            with patch("app.capture_flow.EditorWindow"):
                app.edit_images([(Image.new("RGB", (20, 20)), None)])
            app.tray.showMessage.assert_not_called()
            preview.assert_not_called()
            with patch("app.capture_flow.EditorWindow"):
                app.edit_images([(Image.new("RGB", (20, 20), "red"), None)])
            preview.assert_not_called()
            app.tray.reset_mock()

            app.config.data["save_notification"] = False
            app.saved("capture.png")
            app.tray.showMessage.assert_not_called()
            app.config.data["save_notification"] = True
            app.saved("capture.png")
            self.assertIn("图片已保存", app.tray.showMessage.call_args.args[1])
            app.tray.reset_mock()

            app.config.data["copy_notification"] = False
            app.notify_capture_copied()
            app.tray.showMessage.assert_not_called()
            app.config.data["copy_notification"] = True
            app.notify_capture_copied()
            self.assertIn("已复制到剪贴板", app.tray.showMessage.call_args.args[1])
            app.tray.reset_mock()
            preview.reset_mock()
            app.config.data["copy_notification"] = False
            app.notify_capture_copied(Image.new("RGB", (8, 8), "red"))
            preview.assert_not_called()
            app.config.data["copy_notification"] = True
            app.notify_capture_copied(Image.new("RGB", (8, 8), "red"))
            preview.assert_called_once()
            preview.return_value.show_preview.assert_called_once()
            # 复制通知必须跟随「通知方式」设置（默认 Win11 Toast），而不是硬编码老通知
            self.assertEqual(preview.call_args.kwargs.get("backend"),
                             app.config.data["notification_backend"])
            # 图片不能漏：复制提示必须带图（Win11 Toast 走 hero 图，失败才回退本地预览）
            self.assertIsNotNone(preview.call_args.args[0])
            # 另存为目录必须写回配置并落盘（两个编辑器共用这个处理器）
            app.config.save = Mock()
            app.remember_save_as_dir("D:/另存为目录")
            self.assertEqual(app.config.data["save_as_dir"], "D:/另存为目录")
            app.config.save.assert_called_once()
            original_backend = app.config.data["notification_backend"]
            app.config.data["notification_backend"] = "legacy"
            preview.reset_mock()
            app.capture_notice = None
            app.notify_capture_copied(Image.new("RGB", (8, 8), "red"))
            self.assertEqual(preview.call_args.kwargs.get("backend"), "legacy")
            app.config.data["notification_backend"] = original_backend
            app.capture_notice = None
            preview.reset_mock()
            app.capture_notice = None
            app.config.data["bubble"] = False
            preview.reset_mock()
            app.notify_capture_copied(Image.new("RGB", (8, 8), "red"))
            preview.assert_not_called()
            app.config.data["bubble"] = True

            # 取色通知有独立开关（设置 > 通知 > 取色通知）
            app.config.data["picker_notification"] = False
            app.tray.reset_mock()
            app.notify_color_picked("#abcdef")
            app.tray.showMessage.assert_not_called()
            app.config.data["picker_notification"] = True
            app.notify_color_picked("#abcdef")
            self.assertIn("#abcdef", app.tray.showMessage.call_args.args[1])
            app.tray.reset_mock()
            app.config.data["operation_notification"] = False
            app.notify("status")
            app.tray.showMessage.assert_not_called()
            app.config.data["operation_notification"] = True
            app.notify("status")
            self.assertIn("status", app.tray.showMessage.call_args.args[1])
            self.assertEqual(app.tray.showMessage.call_args.args[3], 2000)
            app.config.data["notification_timeout"] = 6
            app.notify("timed status")
            self.assertEqual(app.tray.showMessage.call_args.args[3], 6000)
            app.tray.reset_mock()

            app.config.data["sticker_notification"] = False
            app.add_sticker(object())
            app.stickers.add.assert_called_once()
            app.tray.showMessage.assert_not_called()
            app.stickers.paste_latest.return_value = True
            app.dispatch("paste")
            app.tray.showMessage.assert_not_called()

            app.config.data["notification_backend"] = "win11toast"
            app.config.data["notification_timeout"] = 20
            app.notification_bridge = Mock()
            with patch("ui.native_toast.show_native_toast", return_value=True) as native:
                app.notify("native message", target_path="capture.png")
                native.assert_called_once()
                self.assertEqual(native.call_args.kwargs["duration"], "long")
                app.tray.showMessage.assert_not_called()
                native.call_args.kwargs["on_click"]()
                app.notification_bridge.activated.emit.assert_called_with(
                    ("capture.png", None))
            app.config.data["notification_backend"] = "legacy"
            app.config.data["sticker_notification"] = True
            app.add_sticker(object())
            self.assertIn("已创建贴图", app.tray.showMessage.call_args.args[1])
            app.tray.reset_mock()
            app.dispatch("paste")
            self.assertIn("已贴上次截图", app.tray.showMessage.call_args.args[1])
            app.tray.reset_mock()
            app.config.data["bubble"] = False
            app.saved("capture.png")
            app.tray.showMessage.assert_not_called()
            preview.reset_mock()
            with patch("app.capture_flow.EditorWindow"):
                app.edit_images([(Image.new("RGB", (20, 20)), None)])
            preview.assert_not_called()

    def test_notification_click_reveals_target_file(self):
        from types import SimpleNamespace
        from main import Application
        from ui.capture_notification import CaptureNotification

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "capture.png"
            target.write_bytes(b"image")
            notification = CaptureNotification(
                Image.new("RGB", (40, 20), "green"), target_path=target)
            activated = []
            notification.file_activated.connect(activated.append)
            notification.show()
            self.app.processEvents()
            QTest.mouseClick(notification, Qt.LeftButton, pos=QPoint(10, 10))
            self.assertEqual(activated, [str(target)])
            notification.close()

            app = Application.__new__(Application)
            app.config = SimpleNamespace(data={"open_notification_file": True})
            app._notification_target_path = str(target)
            app._notification_fallback = None
            with patch("app.notification_flow.subprocess.Popen") as launch:
                self.assertTrue(app.open_notification_target())
            launch.assert_called_once_with(["explorer.exe", f"/select,{target.resolve()}"])

            app.config.data["open_notification_file"] = False
            with patch("app.notification_flow.subprocess.Popen") as launch:
                self.assertFalse(app.open_notification_target())
            launch.assert_not_called()

            app._notification_target_path = None
            app._notification_fallback = "sticker_panel"
            app.open_sticker_panel = Mock()
            self.assertTrue(app.open_notification_target())
            app.open_sticker_panel.assert_called_once_with()
            self.assertFalse(app.open_notification_target(str(target) + ".missing"))
            app.open_sticker_panel.assert_called_once_with()

    def test_startup_notice_and_tray_icon(self):
        import shutil
        import sys
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from core.app_icon import ICON_FILES, app_icon, icon_dir
        from main import Application, drawn_icon, tray_icon

        source_icon_dir = icon_dir()
        self.assertEqual(source_icon_dir,
                 Path(__file__).resolve().parents[1] / "ui" / "assets")
        self.assertTrue(all((source_icon_dir / name).is_file() for name in ICON_FILES))
        icon = tray_icon()
        self.assertFalse(icon.isNull())
        self.assertFalse(app_icon().isNull())
        picture = icon.pixmap(64, 64).toImage()
        opaque = sum(1 for x in range(picture.width()) for y in range(picture.height())
                     if picture.pixelColor(x, y).alpha() > 0)
        self.assertGreater(opaque, 50)
        # 判据不能绑定某个具体图标的设计（2026-10-11：换新图标后中心不再是透明的，
        # 旧断言 pixelColor(中心).alpha() == 0 直接失败）。这里改为验证**真正的不变量**：
        # 托盘图标必须来自图标文件，而不是内置绘制的兜底图 —— 用"等于 app_icon 且不同于 drawn_icon"表达。
        self.assertEqual(picture.constBits().tobytes(),
                         app_icon().pixmap(64, 64).toImage().constBits().tobytes(),
                         "托盘图标必须来自 ui/assets 的图标文件")
        drawn = drawn_icon().pixmap(64, 64).toImage()
        self.assertNotEqual(picture.constBits().tobytes(), drawn.constBits().tobytes(),
                            "托盘图标不得退化成内置绘制的兜底图标")

        with tempfile.TemporaryDirectory() as bundle:
            bundled_icon_dir = Path(bundle) / "ui" / "assets"
            bundled_icon_dir.mkdir(parents=True)
            for name in ICON_FILES:
                shutil.copy2(source_icon_dir / name, bundled_icon_dir / name)
            with patch.object(sys, "_MEIPASS", bundle, create=True):
                self.assertEqual(icon_dir(), bundled_icon_dir)
                self.assertFalse(app_icon().isNull())

        # 未显式设置图标的窗口应继承应用级图标（设置、编辑器等窗口都靠这条链路）。
        from PySide6.QtWidgets import QApplication, QWidget

        application = QApplication.instance()
        application.setWindowIcon(icon)
        window = QWidget()
        self.assertFalse(window.windowIcon().isNull())
        self.assertEqual(window.windowIcon().cacheKey(), icon.cacheKey())
        window.close()

        app = Application.__new__(Application)
        app.config = SimpleNamespace(data=DEFAULTS.copy())
        app.config.data["notification_backend"] = "legacy"
        app.tray = Mock()
        with patch("main.QApplication.beep"):
            app.announce_startup()
            self.assertIn("已启动", app.tray.showMessage.call_args.args[1])
            app.tray.reset_mock()
            app.config.data["bubble"] = False
            app.announce_startup()
            app.tray.showMessage.assert_not_called()

    def test_tray_menu_dispatches_image_edit_actions(self):
        from ui.tray_menu import make_tray_menu

        calls = []
        menu = make_tray_menu(self.app, lambda: calls.append("capture"), lambda: None,
                              lambda: None, lambda: calls.append("clipboard"),
                              lambda: calls.append("open"),
                              {"capture": "ctrl+shift+f1", "edit_clipboard": "ctrl+alt+v",
                               "open_image": "ctrl+alt+e"})
        actions = {action.text(): action for action in menu.actions() if not action.isSeparator()}
        clipboard = next(action for label, action in actions.items() if label.startswith("编辑剪贴板图片"))
        open_image = next(action for label, action in actions.items() if label.startswith("打开并编辑图片"))
        self.assertNotEqual(clipboard.icon().pixmap(24, 24).toImage(),
                    open_image.icon().pixmap(24, 24).toImage())
        self.assertIn("Ctrl+Alt+V", clipboard.text())
        self.assertIn("Ctrl+Alt+E", open_image.text())
        self.assertTrue(all(action.toolTip().strip() for action in actions.values()))
        self.assertIn("Ctrl+Shift+F1", next(iter(actions)))
        clipboard.trigger()
        open_image.trigger()
        self.assertEqual(calls, ["clipboard", "open"])
        menu = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None,
                      hotkeys={"open_sticker_file": "shift+f3"},
                      open_sticker=lambda: calls.append("sticker-hotkey"))
        new_sticker = next(action for action in menu.actions()
                   if action.text().startswith("从文件打开新贴图"))
        self.assertIn("Shift+F3", new_sticker.text())
        new_sticker.trigger()
        self.assertEqual(calls[-1], "sticker-hotkey")
        menu = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None,
                              open_sticker=lambda: calls.append("sticker"))
        next(action for action in menu.actions()
             if action.text() == "从文件打开新贴图").trigger()
        self.assertEqual(calls[-1], "sticker")

    def test_single_instance_lock_rejects_duplicate_and_releases(self):
        from main import acquire_single_instance_lock

        with tempfile.TemporaryDirectory() as folder:
            lock_path = Path(folder) / "screensnap.lock"
            first_lock = acquire_single_instance_lock(lock_path)
            self.assertIsNotNone(first_lock)
            try:
                self.assertIsNone(acquire_single_instance_lock(lock_path))
            finally:
                first_lock.unlock()

            next_lock = acquire_single_instance_lock(lock_path)
            self.assertIsNotNone(next_lock)
            next_lock.unlock()

    def test_duplicate_startup_reports_existing_tray_instance(self):
        import io
        from main import notify_existing_instance

        output = io.StringIO()
        with patch("main.os.name", "posix"), patch("main.sys.stderr", output):
            notify_existing_instance()
        self.assertIn("已经在运行", output.getvalue())
        self.assertIn("系统托盘", output.getvalue())


    def test_native_toast_uses_screen_snap_identity_and_falls_back(self):
        """Toast 用 ScreenSnap 应用身份；该身份不可用时退回默认身份重发一次。"""
        import sys
        import time
        import types

        from core.constants import APP_USER_MODEL_ID
        from ui import native_toast

        calls = []
        module = types.ModuleType("win11toast")

        def first_fails(title, body="", **kwargs):
            calls.append(dict(kwargs))
            if len(calls) == 1:
                raise RuntimeError("AUMID 未注册")

        module.toast = first_fails
        original = sys.modules.get("win11toast")
        sys.modules["win11toast"] = module
        failures = []
        try:
            self.assertTrue(native_toast.show_native_toast(
                "标题", "正文", None, None, failures.append))
            deadline = time.monotonic() + 3.0
            while len(calls) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            if original is None:
                sys.modules.pop("win11toast", None)
            else:
                sys.modules["win11toast"] = original
        self.assertEqual(calls[0].get("app_id"), APP_USER_MODEL_ID)   # 首选 ScreenSnap 身份
        self.assertEqual(len(calls), 2)
        self.assertNotIn("app_id", calls[1])                          # 回退默认身份
        self.assertEqual(failures, [])                                # 回退成功 → 不报失败


    def test_editor_save_with_copy_notifies_once(self):
        """独立编辑器「保存」（含复制）只发一条带图保存通知，不再多发纯文字通知。

        回归：save() 里复制后还 status.emit("已复制保存内容")，而应用把 status 接到 notify，
        于是同时收到「带图的保存通知」+「纯文字复制通知」两条。
        """
        from config.config_manager import DEFAULTS
        from editor.editor_window import EditorWindow

        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, save_dir=folder, filename="save_once",
                            save_format="png", copy_saved_path=True,
                            copy_saved_image=True)
            editor = EditorWindow(Image.new("RGB", (30, 20), "white"), settings)
            statuses, saved = [], []
            editor.status.connect(statuses.append)
            editor.image_saved.connect(lambda path, image: saved.append(path))
            try:
                path = editor.save(copy_to_clipboard=True)
                self.assertEqual(len(saved), 1)          # 保存通知一条
                self.assertIsNotNone(saved[0])
                self.assertEqual(statuses, [])           # 不再多发纯文字通知
                from PySide6.QtGui import QGuiApplication
                self.assertEqual(QGuiApplication.clipboard().text(), str(path))  # 复制仍生效
            finally:
                editor.close()


    def test_editor_save_failure_notifies_and_keeps_editor_open(self):
        """保存失败时必须发通知（status → 应用通知），并保持编辑器打开以便重试。"""
        from config.config_manager import DEFAULTS
        from editor.editor_window import EditorWindow

        with tempfile.TemporaryDirectory() as folder:
            blocked = Path(folder) / "blocked"
            blocked.write_text("not a directory", encoding="utf-8")
            editor = EditorWindow(Image.new("RGB", (30, 20), "white"),
                                  dict(DEFAULTS, save_dir=str(blocked), filename="fail"))
            statuses = []
            editor.status.connect(statuses.append)
            closed = []
            editor.close = lambda *args, **kwargs: closed.append(True)   # 不应被调用
            editor.execute("save")
            self.assertTrue(statuses and "保存失败" in statuses[0])       # 有失败通知
            self.assertEqual(closed, [])                                  # 编辑器保持打开


if __name__ == "__main__":  # 支持 python tests/test_app.py
    unittest.main()
