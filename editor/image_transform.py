"""不依赖 Qt 的图片变换。"""

from PIL import Image


def transform(image, operation, degrees=0):
    """返回新图片而不修改传入对象，便于历史栈保留变换前版本。"""
    if operation == "angle" and degrees % 360 == 0:
        return image.copy()
    operations = {
        "left": lambda: image.rotate(90, expand=True),
        "right": lambda: image.rotate(-90, expand=True),
        "half": lambda: image.rotate(180, expand=True),
        "horizontal": lambda: image.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
        "vertical": lambda: image.transpose(Image.Transpose.FLIP_TOP_BOTTOM),
        "angle": lambda: image.convert("RGBA").rotate(
            -degrees, expand=True, fillcolor=(0, 0, 0, 0)),
    }
    return operations[operation]()