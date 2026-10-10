"""mss 捕获虚拟桌面，保留屏幕物理坐标。"""

from io import BytesIO
import logging
import os

import mss
from PIL import Image
from PySide6.QtGui import QImage

# `mss.mss(...)` 已被上游标记废弃（会往 stderr 打 DeprecationWarning，用户实测可见），
# 新版本提供 `mss.MSS` 类；用 getattr 兜住旧版本。模块级别名同时便于测试替换。
# 注意：旧的 `with_cursor` 参数只在 Linux 有效（Windows 上一向被忽略），
# 光标由本模块的 native_cursor() 另行合成，因此这里不再传该参数，行为不变。
_mss_factory = getattr(mss, "MSS", mss.mss)


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


def capture(cursor=False, alternatives=False, gap_fill="transparent"):
    """按显示器合成虚拟桌面；屏幕间隙可透明、纯黑或纯白。"""
    with _mss_factory() as grabber:
        # monitors[0] 是虚拟桌面边界；逐屏抓取以避免 MSS 把屏幕间隙烘焙为实色。
        bounds = dict(grabber.monitors[0])
        monitors = [dict(monitor) for monitor in grabber.monitors[1:]]
        # 显示器之间的虚拟桌面空隙不属于任何一块屏，按设置填透明/纯黑/纯白；
        # 逐屏 alpha_composite 到同一张 RGBA 画布，避免 MSS 把空隙烘焙成实色。
        gap_colors = {
            "transparent": (0, 0, 0, 0),
            "black": (0, 0, 0, 255),
            "white": (255, 255, 255, 255),
        }
        fill = gap_colors.get(gap_fill, gap_colors["transparent"])
        # 只有“透明间隙”才需要 RGBA；纯黑/纯白间隙用 RGB 画布即可 —— 省 1/4 内存，
        # 贴屏从逐像素 alpha 合成变成纯拷贝，多屏大分辨率下抓屏更快。
        transparent = fill[3] == 0
        image = Image.new("RGBA" if transparent else "RGB",
                          (bounds["width"], bounds["height"]),
                          fill if transparent else fill[:3])
        for monitor in monitors:
            shot = grabber.grab(monitor)
            screen = Image.frombytes("RGB", shot.size, shot.rgb)
            offset = (monitor["left"] - bounds["left"],
                      monitor["top"] - bounds["top"])
            if transparent:
                image.alpha_composite(screen.convert("RGBA"), offset)
            else:
                image.paste(screen, offset)
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