"""截图保存格式与质量的统一处理。"""

import logging
import os
import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QImage, QPainter

# 保存格式 -> (Qt 保存器名称, 文件扩展名)。键同时用于配置值与设置页选项。
SAVE_FORMATS = {
    "png": ("PNG", "png"),
    "jpg": ("JPEG", "jpg"),
    "webp": ("WEBP", "webp"),
    "bmp": ("BMP", "bmp"),
}

# 需要质量参数且不支持透明通道的格式。
LOSSY_FORMATS = ("jpg", "webp")
OPAQUE_FORMATS = ("jpg", "bmp")


def saved_extension(settings):
    """返回配置对应的扩展名；未知格式回退到 png。"""
    return SAVE_FORMATS.get(str(settings.get("save_format", "png")).lower(), SAVE_FORMATS["png"])[1]


def matches_saved_format(path, settings):
    """Return whether an existing path matches the configured encoder format."""
    return (path is not None and
            Path(path).suffix.casefold() == f".{saved_extension(settings)}")


def saved_patterns():
    """历史图片读取时使用，覆盖所有可保存格式。"""
    return tuple(f"*.{extension}" for _, extension in SAVE_FORMATS.values())


def flatten(image, settings=None):
    """透明区域合成到配置底色，供不支持 alpha 的格式使用。"""
    if not image.hasAlphaChannel():
        return image
    flat = QImage(image.size(), QImage.Format_RGB32)
    flat.fill(QColor(str((settings or {}).get("save_background", "#ffffff"))))
    painter = QPainter(flat)
    painter.drawImage(0, 0, image)
    painter.end()
    return flat


def save_image(image, path, settings):
    """按配置格式先写同目录临时文件，再原子替换目标；无损格式忽略质量参数。"""
    key = str(settings.get("save_format", "png")).lower()
    writer, _ = SAVE_FORMATS.get(key, SAVE_FORMATS["png"])
    quality = int(settings.get("save_quality", 90))
    target = flatten(image, settings) if key in OPAQUE_FORMATS else image
    path = Path(path)
    temporary_path = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temporary_path = Path(temporary_name)
        os.close(descriptor)
        if not target.save(str(temporary_path), writer,
                           quality if key in LOSSY_FORMATS else -1):
            return False
        os.replace(str(temporary_path), str(path))
        temporary_path = None
        return True
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as error:
                logging.getLogger("screensnap").warning(
                    "无法清理图片保存临时文件 %s: %s", temporary_path, error)
