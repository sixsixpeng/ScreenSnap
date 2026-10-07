"""Windows native toast helpers with stable local image resources."""

import logging
import os
from pathlib import Path
from threading import Thread
from uuid import uuid4

from PySide6.QtGui import QImage
from PIL import Image as PillowImage

from core.path_utils import data_dir
from core.screen_capture import to_qimage


logger = logging.getLogger("screensnap")


def native_toast_duration(timeout_seconds):
    """Win11 Toast 仅接受系统定义的 short/long，不支持精确秒数。"""
    timeout_seconds = int(timeout_seconds or 0)
    return "short" if 0 < timeout_seconds <= 7 else "long"


def cache_toast_image(image):
    """Write a toast image to a durable local path understood by WinRT."""
    if isinstance(image, PillowImage.Image):
        image = to_qimage(image)
    if not isinstance(image, QImage) or image.isNull():
        raise ValueError("native toast image is empty")
    folder = data_dir() / "toast_cache"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"toast_{uuid4().hex}.png"
    if not image.save(str(path), "PNG"):
        raise OSError(f"cannot cache toast image: {path}")
    cached = sorted(folder.glob("toast_*.png"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old_path in cached[32:]:
        try:
            old_path.unlink()
        except OSError as error:
            logger.warning("cannot remove old toast image %s: %s", old_path, error)
    return path


def show_native_toast(title, body="", image_path=None, on_click=None,
                      on_failed=None, duration=None):
    """Post a WinRT toast off the Qt thread; return False if unavailable."""
    if os.name != "nt":
        return False
    try:
        from win11toast import toast
    except ImportError as error:
        logger.info("win11toast is unavailable; using Qt notification: %s", error)
        return False

    kwargs = {}
    if duration in ("short", "long"):
        kwargs["duration"] = duration
    if image_path is not None:
        kwargs["image"] = {"src": str(Path(image_path).resolve()), "placement": "hero"}

    def clicked(result):
        if on_click is not None:
            on_click(result)
        else:
            logger.debug("Windows 原生通知被点击: %s", result)

    def dismissed(result):
        logger.debug("Windows 原生通知关闭: %s", result)

    def failed(result):
        logger.warning("Windows 原生通知报告失败: %s", result)
        if on_failed is not None:
            on_failed(result)

    kwargs.update(on_click=clicked, on_dismissed=dismissed, on_failed=failed)

    def post():
        try:
            toast(title, body, **kwargs)
        except OSError as error:
            logger.debug("native Windows toast unavailable; using local notification: %s",
                         error)
            if on_failed is not None:
                on_failed(error)
        except Exception as error:
            logger.warning("native Windows toast failed: %s", error)
            if on_failed is not None:
                on_failed(error)

    Thread(target=post, name="ScreenSnap-WinToast", daemon=True).start()
    return True
