"""用户界面包。"""

from importlib import import_module

_EXPORTS = {
	"SettingsWindow": "ui.settings_window",
	"CaptureNotification": "ui.capture_notification",
	"make_tray_menu": "ui.tray_menu",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
	"""按需公开顶层 UI 组件，避免编辑器导入子控件时触发循环引用。"""
	if name not in _EXPORTS:
		raise AttributeError(name)
	value = getattr(import_module(_EXPORTS[name]), name)
	globals()[name] = value
	return value