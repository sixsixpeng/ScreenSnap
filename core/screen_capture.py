"""mss 捕获虚拟桌面，保留屏幕物理坐标。"""

from io import BytesIO
import logging
import os

import mss
from PIL import Image
from PySide6.QtGui import QImage


def native_cursor():
    if os.name != "nt":
        return None
    import win32con
    import win32gui
    import win32ui

    visible, handle, position = win32gui.GetCursorInfo()
    if not visible or not handle:
        return None
    is_icon, hotspot_x, hotspot_y, mask_handle, color_handle = win32gui.GetIconInfo(handle)
    try:
        bitmap = win32gui.GetObject(color_handle or mask_handle)
        width = bitmap.bmWidth
        height = bitmap.bmHeight if color_handle else bitmap.bmHeight // 2
        if not width or not height:
            return None
        desktop = win32gui.GetDC(0)
        screen = win32ui.CreateDCFromHandle(desktop)
        memory = screen.CreateCompatibleDC()
        surface = win32ui.CreateBitmap()
        surface.CreateCompatibleBitmap(screen, width, height)
        previous = memory.SelectObject(surface)
        try:
            frames = []
            for background in (0x000000, 0xFFFFFF):
                memory.FillSolidRect((0, 0, width, height), background)
                win32gui.DrawIconEx(memory.GetSafeHdc(), 0, 0, handle, width, height,
                                    0, 0, win32con.DI_NORMAL)
                frames.append(Image.frombytes("RGB", (width, height), surface.GetBitmapBits(True),
                                              "raw", "BGRX", 0, 1))
        finally:
            memory.SelectObject(previous)
            win32gui.DeleteObject(surface.GetHandle())
            memory.DeleteDC()
            screen.DeleteDC()
            win32gui.ReleaseDC(0, desktop)
        pixels = []
        for dark, light in zip(frames[0].getdata(), frames[1].getdata()):
            alpha = max(0, min(255, 255 - round(sum(light[index] - dark[index]
                                                  for index in range(3)) / 3)))
            pixels.append(tuple(min(255, round(channel * 255 / alpha)) for channel in dark) +
                          (alpha,) if alpha else (0, 0, 0, 0))
        cursor = Image.new("RGBA", (width, height))
        cursor.putdata(pixels)
        return cursor, position[0] - hotspot_x, position[1] - hotspot_y
    finally:
        win32gui.DeleteObject(mask_handle)
        if color_handle:
            win32gui.DeleteObject(color_handle)


def capture(cursor=False, alternatives=False):
    """返回虚拟桌面像素、物理边界及显示器列表，可选光标反向版本。"""
    with mss.mss(with_cursor=False) as grabber:
        # monitors[0] 包含整个虚拟桌面，包括负坐标和显示器间的空隙。
        bounds = dict(grabber.monitors[0])
        shot = grabber.grab(bounds)
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        monitors = [dict(monitor) for monitor in grabber.monitors[1:]]
    logger = logging.getLogger("screensnap")
    logger.debug("捕获虚拟桌面: %sx%s，显示器 %d 个", image.width, image.height, len(monitors))
    with_pointer = image.copy()
    try:
        pointer = native_cursor()
    except Exception as error:  # 指针形状偶发无法读取，降级为不含光标的截图。
        logger.warning("无法读取鼠标指针，本次截图不含光标: %s", error)
        pointer = None
    if pointer is not None:
        cursor_image, left, top = pointer
        with_pointer.paste(cursor_image, (left - bounds["left"], top - bounds["top"]), cursor_image)
    selected, other = (with_pointer, image) if cursor else (image, with_pointer)
    return (selected, bounds, monitors, other) if alternatives else (selected, bounds, monitors)


def to_qimage(image):
    """直接复制常用格式的像素；其他格式经 PNG 转换为独立 QImage。"""
    if image.mode in ("RGB", "RGBA"):
        pixels = image.tobytes()
        format_ = QImage.Format_RGB888 if image.mode == "RGB" else QImage.Format_RGBA8888
        return QImage(pixels, image.width, image.height,
                      image.width * len(image.getbands()), format_).copy()
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    result = QImage.fromData(buffer.getvalue())
    if result.isNull():
        raise ValueError("无法加载截图像素")
    return result


def qimage_to_pillow(image):
    """通过原始 RGBA 像素缓冲区转为独立的 Pillow 图像。"""
    if image.isNull():
        raise ValueError("剪贴板中没有图片")
    rgba = image.convertToFormat(QImage.Format_RGBA8888)
    return Image.frombuffer("RGBA", (rgba.width(), rgba.height()), rgba.constBits(),
                            "raw", "RGBA", rgba.bytesPerLine(), 1).copy()