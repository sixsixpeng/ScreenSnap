"""导出时对编辑结果应用圆角、边框和阴影。"""

from PIL import Image, ImageChops, ImageDraw, ImageFilter


def copy_saved_to_clipboard(path, image, settings, force_image=False):
    """保存后按设置把结果复制到剪贴板：路径与图像各自受开关控制。

    两个开关都关时不动剪贴板（设置优先）；force_image 用于「复制图像」这类显式命令。
    """
    copy_path = bool(settings.get("copy_saved_path"))
    copy_image = bool(settings.get("copy_saved_image")) or bool(force_image)
    if not (copy_path or copy_image):
        return False
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QGuiApplication

    payload = QMimeData()
    if copy_path:
        payload.setText(str(path))
    if copy_image:
        payload.setImageData(image)
    QGuiApplication.clipboard().setMimeData(payload)
    return True


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
