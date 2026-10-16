"""导出时对编辑结果应用圆角、边框和阴影。"""

import os

from logger import get_logger

from PIL import Image, ImageChops, ImageDraw, ImageFilter


def clipboard_available():
    """探测剪贴板能否打开：被别的程序占着时 Qt 的 setMimeData 会内部重试并刷几百行

    `qt.qpa.mime: Retrying to obtain clipboard` / `OleSetClipboard: Failed … OpenClipboard`
    （2026-10-11 真机实测：一次保存刷了 7 秒日志）。非 Windows 恒为可用。
    """
    if os.name != "nt":
        return True
    import ctypes

    user32 = ctypes.windll.user32
    if user32.OpenClipboard(None):
        user32.CloseClipboard()
        return True
    return False


def copy_saved_to_clipboard(path, image, settings, force_image=False,
                            clipboard=None):
    """主动输出按设置复制：路径文字与图像独立，无落盘路径时只输出图像。

    两个开关都关时不动剪贴板；只写图片和路径文字，不附加文件对象格式。
    内部缓存、恢复和预览不得调用本函数。失败返回 False，不重试、不增加通知。
    """
    logger = get_logger()
    copy_path = bool(settings.get("copy_saved_path")) and path is not None
    copy_image = bool(settings.get("copy_saved_image")) or bool(force_image)
    if not (copy_path or copy_image):
        logger.debug("输出复制跳过：图片/路径开关关闭或没有有效保存路径")
        return False
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QGuiApplication

    try:
        if not clipboard_available():
            logger.warning("剪贴板被其他程序占用，本次输出跳过复制（不重试）")
            return False
        if copy_image and isinstance(image, Image.Image):
            from core.screen_capture import to_qimage

            image = to_qimage(image)
        if copy_image and (image is None or image.isNull()):
            logger.warning("输出复制失败：图像为空")
            return False
        payload = QMimeData()
        if copy_path:
            payload.setText(str(path))
        if copy_image:
            payload.setImageData(image)
        board = clipboard if clipboard is not None else QGuiApplication.clipboard()
        board.setMimeData(payload)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        logger.warning("输出复制失败（不重试）: %s", error)
        return False
    logger.debug("输出已复制到剪贴板：图片=%s 路径=%s", copy_image, copy_path)
    return True


def copy_image_to_clipboard(image, clipboard=None):
    """显式仅复制图像，不受自动复制开关影响，也不附加保存路径。"""
    return copy_saved_to_clipboard(None, image, {}, force_image=True, clipboard=clipboard)


def apply_output_effects(image, settings, round_corners=False, corner_radius=None):
    """在最终导出图像上应用非破坏性装饰，不改动编辑画布和撤销历史。"""
    is_qimage = not isinstance(image, Image.Image)
    if is_qimage:
        from core.screen_capture import qimage_to_pillow

        image = qimage_to_pillow(image)
    result = image.convert("RGBA")
    width, height = result.size
    radius = (settings.get("editor_image_corner_radius", 16)
              if corner_radius is None else corner_radius)
    radius = max(0, min(int(radius), width // 2, height // 2))

    round_mask = None
    if round_corners and radius:
        scale = 4
        round_mask = Image.new("L", (width * scale, height * scale), 0)
        ImageDraw.Draw(round_mask).rounded_rectangle(
            (0, 0, width * scale - 1, height * scale - 1),
            radius * scale, fill=255)
        round_mask = round_mask.resize(result.size, Image.Resampling.LANCZOS)

    border_width = settings.get("editor_image_border_width", 2)
    if settings.get("editor_image_border_enabled", False) and border_width:
        scale = 4
        inset = min(max(1, int(border_width)), min(width, height) // 2)
        border_mask = Image.new("L", (width * scale, height * scale), 0)
        draw = ImageDraw.Draw(border_mask)
        outer_radius = radius if round_corners else 0
        draw.rounded_rectangle((0, 0, width * scale - 1, height * scale - 1),
                               outer_radius * scale, fill=255)
        if width > inset * 2 and height > inset * 2:
            inner_radius = max(0, radius - inset) if round_corners else 0
            draw.rounded_rectangle((inset * scale, inset * scale,
                                    (width - inset) * scale - 1,
                                    (height - inset) * scale - 1),
                                   inner_radius * scale, fill=0)
        border_mask = border_mask.resize(result.size, Image.Resampling.LANCZOS)
        border_layer = Image.new("RGBA", result.size,
                                 settings.get("editor_image_border_color", "#ffffff"))
        border_layer.putalpha(border_mask)
        result = Image.alpha_composite(result, border_layer)

    if round_mask is not None:
        result.putalpha(ImageChops.multiply(result.getchannel("A"), round_mask))
        alpha = result.getchannel("A")
        visible = alpha.point(lambda value: 255 if value else 0)
        empty = Image.new("L", result.size, 0)
        channels = [Image.composite(channel, empty, visible)
                    for channel in result.split()[:3]]
        result = Image.merge("RGBA", (*channels, alpha))

    if settings.get("editor_image_shadow_enabled", False):
        blur = settings.get("editor_image_shadow_size", 12)
        strength = settings.get("editor_image_shadow_strength", 25)
        if blur and strength:
            padding = blur * 2
            alpha = result.getchannel("A").filter(ImageFilter.GaussianBlur(blur))
            color = Image.new("RGBA", result.size, settings.get("editor_image_shadow_color", "#000000"))
            color.putalpha(alpha.point(lambda value: value * strength // 100))
            canvas = Image.new("RGBA", (width + padding * 2, height + padding * 2), (0, 0, 0, 0))
            canvas.alpha_composite(color, (padding, padding))
            canvas.alpha_composite(result, (padding, padding))
            result = canvas

    if is_qimage:
        from core.screen_capture import to_qimage

        return to_qimage(result)
    return result
