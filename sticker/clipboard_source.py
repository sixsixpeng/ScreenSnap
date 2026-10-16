"""把剪贴板中的图片、文件、颜色和文字统一渲染成可贴出的图像。"""

import os
import re
from html import unescape
from pathlib import Path

from PySide6.QtCore import QFileInfo, Qt, QRectF
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QImage,
                           QPainter, QTextDocument)
from PySide6.QtWidgets import QFileIconProvider

from logger import get_logger

# 文件贴图最多展示的条目数量，超出部分只统计数量。
MAX_FILES = 8

LONG_HEX = re.compile(r"[0-9a-fA-F]{6}")
SHORT_HEX = re.compile(r"[0-9a-fA-F]{3}")


class ClipboardSource:
    """统一描述剪贴板内容；image 始终是可以直接贴出的 QImage。"""

    def __init__(self, kind, image, text="", paths=None, html=""):
        self.kind = kind
        self.image = image
        self.text = text
        self.paths = [str(path) for path in (paths or [])]
        self.html = str(html or "")

    def origin(self):
        """写入贴图会话的最小元信息，恢复后仍可用于复制或打开。"""
        data = {"kind": self.kind}
        if self.kind in ("text", "color"):
            data["text"] = self.text
        if self.kind == "html":
            data["text"] = self.text
            data["html"] = self.html
        elif self.kind == "files":
            data["paths"] = self.paths
        return data

    def label(self):
        """托盘提示与贴图管理窗口中显示的简短说明。"""
        if self.kind == "text":
            first = self.text.strip().splitlines()[0] if self.text.strip() else ""
            return f"文字 · {first[:18]}" if first else "文字"
        if self.kind == "html":
            first = self.text.strip().splitlines()[0] if self.text.strip() else ""
            return f"富文本 · {first[:18]}" if first else "富文本"
        if self.kind == "color":
            return f"颜色 {self.text}"
        if self.kind == "files":
            name = Path(self.paths[0]).name if self.paths else ""
            extra = f" 等 {len(self.paths)} 项" if len(self.paths) > 1 else ""
            return f"文件 · {name}{extra}"
        return "剪贴板图片"


def parse_color(text):
    """识别常见颜色写法，其他文本返回 None。

    支持：#RGB、#RRGGBB、#RRGGBBAA（忽略 alpha）、裸六位十六进制、0xRRGGBB、
    rgb(r,g,b) / rgba(r,g,b,a)、以及裸的 R,G,B（2026-10-11 扩展：用户从别的工具复制颜色时
    常常不是纯十六进制，识别不了就会退化成"绿底文字卡片"）。
    """
    value = str(text or "").strip().strip('"\'')
    if not value:
        return None
    lowered = value.lower().replace(" ", "")
    body = None
    if lowered.startswith("#"):
        body = lowered[1:]
    elif lowered.startswith("0x"):
        body = lowered[2:]
    elif LONG_HEX.fullmatch(lowered):
        return "#" + lowered
    if body is not None:
        if len(body) == 8 and LONG_HEX.fullmatch(body[:6]) and re.fullmatch(r"[0-9a-f]{2}", body[6:]):
            return "#" + body[:6]  # #RRGGBBAA：忽略 alpha（QColor 也这么处理）
        if len(body) == 6 and LONG_HEX.fullmatch(body):
            return "#" + body
        if SHORT_HEX.fullmatch(body):
            return "#" + "".join(char * 2 for char in body).lower()
        return None
    numbers = re.fullmatch(r"rgba?\(([^)]*)\)", lowered)
    if numbers is None:
        numbers = re.fullmatch(r"(\d{1,3}),(\d{1,3}),(\d{1,3})", lowered)
        if numbers is not None:
            channels = numbers.groups()
        else:
            return None
    else:
        parts = [part for part in numbers.group(1).split(",") if part]
        if len(parts) < 3:
            return None
        channels = parts[:3]
    try:
        rgb = [max(0, min(255, int(float(channel)))) for channel in channels]
    except (TypeError, ValueError):
        return None
    return "#%02x%02x%02x" % tuple(rgb)

def card_font(settings, key, fallback_size):
    """文字类贴图共用字体设置，未指定字体时使用系统默认中文字体。"""
    font = QFont(str(settings.get(key) or "") or "Microsoft YaHei")
    font.setPointSize(int(settings.get("text_sticker_font_size", fallback_size)))
    return font


def wrap_lines(metrics, text, max_width, limit):
    """按字符折行，中文没有空格也能换行；超出行数上限时截断。"""
    lines = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ").split("\n"):
        current = ""
        for char in paragraph:
            if current and metrics.horizontalAdvance(current + char) > max_width:
                lines.append(current)
                current = char
            else:
                current += char
            if len(lines) >= limit and not current:
                break
        lines.append(current)
        if len(lines) >= limit:
            break
    return lines[:limit]


def render_text_card(text, settings):
    """把纯文本渲染成便签样式的卡片，背景与前景色可在设置中调整。"""
    font = card_font(settings, "text_sticker_font", 18)
    metrics = QFontMetrics(font)
    max_width = int(settings.get("text_sticker_width", 420))
    limit = int(settings.get("text_sticker_lines", 40))
    padding = max(12, font.pointSize())
    lines = wrap_lines(metrics, text, max_width, limit)
    if not lines:
        lines = [""]
    if len(lines) == limit:
        lines[-1] = metrics.elidedText(lines[-1] + " …", Qt.ElideRight, max_width)
    spacing = metrics.lineSpacing()
    width = min(max_width, max(metrics.horizontalAdvance(line) for line in lines)) + padding * 2
    height = spacing * len(lines) + padding * 2
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor(str(settings.get("text_sticker_background", "#1f6f5c"))))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setFont(font)
    painter.setPen(QColor(str(settings.get("text_sticker_color", "#ffffff"))))
    for index, line in enumerate(lines):
        painter.drawText(padding, padding + index * spacing + metrics.ascent(), line)
    painter.end()
    return image


def render_html_card(markup, text, settings):
    """保留网页复制的段落、强调、列表、链接等结构绘制为贴图。"""
    font = card_font(settings, "text_sticker_font", 18)
    width = int(settings.get("text_sticker_width", 420))
    padding = max(12, font.pointSize())
    document = QTextDocument()
    document.setDefaultFont(font)
    document.setDocumentMargin(0)
    document.setHtml(markup[:1_000_000])
    document.setTextWidth(max(40, width - padding * 2))
    size = document.size().toSize()
    max_height = max(80, QFontMetrics(font).lineSpacing() *
                     int(settings.get("text_sticker_lines", 40)))
    height = min(size.height(), max_height) + padding * 2
    card = QImage(min(width, max(100, size.width() + padding * 2)), height,
                  QImage.Format_ARGB32_Premultiplied)
    card.fill(QColor(str(settings.get("text_sticker_background", "#1f6f5c"))))
    painter = QPainter(card)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.translate(padding, padding)
    document.drawContents(painter, QRectF(0, 0, card.width() - padding * 2, max_height))
    painter.end()
    get_logger().debug(
        "渲染剪贴板富文本贴图: html_bytes=%d text_chars=%d output=%dx%d",
        len(markup.encode("utf-8", errors="replace")), len(text), card.width(), card.height())
    return card


def render_color_card(color, settings):
    """整块填充颜色，并按设置标注 1-4 行色值/色名（2026-10-11 方案 A+B）。

    标签内容由 `color_sticker_labels` 决定：follow（跟随取色复制格式）/ hex / rgb / rgba / hsl /
    all（三行 + 色名）/ name（色值 + 色名）/ off（只显示色块）。文字颜色按背景亮度自动取黑或白，
    避免浅色贴图上白字看不见；`all` 模式还会在卡片底部画一条 4 段色阶条。
    """
    from core.color_format import label_lines

    width = int(settings.get("color_sticker_width", 260))
    height = int(settings.get("color_sticker_height", 150))
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor(color))
    mode = str(settings.get("color_sticker_labels", "follow") or "follow").lower()
    if mode == "follow":
        mode = str(settings.get("picker_copy_format", "all") or "all").lower()
    labels = []
    if mode != "off" and settings.get("color_sticker_value", True):
        base = "hex" if mode == "name" else mode
        labels = label_lines(color, base, with_name=mode in ("all", "name"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.TextAntialiasing)
    # 色值底板是圆角矩形：一并开启几何抗锯齿（2026-10-16 统一补齐）。
    painter.setRenderHint(QPainter.Antialiasing, True)
    if labels:
        font = card_font(settings, "text_sticker_font", 14)
        metrics = QFontMetrics(font)
        painter.setFont(font)
        line_height = metrics.height()
        pad = 8
        text_width = max(metrics.horizontalAdvance(text) for text in labels)
        box_width = min(width - 20, text_width + pad * 2)
        box_height = line_height * len(labels) + pad * 2
        box_top = max(0, height - box_height - 10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(10, box_top, box_width, box_height, 5, 5)
        # 自动对比色：浅色背景用黑字、深色背景用白字（原来的固定白字在浅色贴图上看不见）。
        rgb = QColor(color)
        luminance = 0.299 * rgb.red() + 0.587 * rgb.green() + 0.114 * rgb.blue()
        painter.setPen(QColor("black") if luminance > 150 else QColor("white"))
        for index, text in enumerate(labels):
            painter.drawText(10 + pad, box_top + pad + line_height * index + metrics.ascent(), text)
    if mode == "all":
        # 色阶条：4 段同色系明度，贴在卡片底部（只在“全部”模式下画）。
        base = QColor(color)
        painter.setPen(Qt.NoPen)
        for index, factor in enumerate((0.25, 0.5, 0.75, 1.0)):
            painter.setBrush(QColor.fromHsv(max(0, base.hue()), base.saturation(), int(255 * factor)))
            painter.drawRect(int(width * index / 4), height - 10, int(width / 4) + 1, 10)
    painter.end()
    return image


def file_icon(path):
    """取系统文件图标；无法提供时返回空图标，不影响卡片渲染。"""
    try:
        return QFileIconProvider().icon(QFileInfo(str(path)))
    except (RuntimeError, OSError):
        return None


def render_file_card(paths, settings):
    """渲染文件列表卡片：系统图标 + 文件名，可选显示所在目录。"""
    font = card_font(settings, "text_sticker_font", 12)
    metrics = QFontMetrics(font)
    icon_size = 40
    padding = 14
    gap = 8
    show_path = bool(settings.get("file_sticker_path", True))
    shown = paths[:max(1, int(settings.get("file_sticker_max", MAX_FILES)))]
    row_height = max(icon_size, metrics.lineSpacing() * (2 if show_path else 1)) + gap
    width = int(settings.get("file_sticker_width", 360))
    height = padding * 2 + row_height * len(shown)
    if len(paths) > len(shown):
        height += metrics.lineSpacing()
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor("#f4f7f8"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setFont(font)
    text_left = padding + icon_size + 10
    text_width = width - text_left - padding
    for index, path in enumerate(shown):
        top = padding + index * row_height
        icon = file_icon(path)
        if icon is not None and not icon.isNull():
            painter.drawPixmap(padding, top + (row_height - gap - icon_size) // 2,
                               icon.pixmap(icon_size, icon_size))
        painter.setPen(QColor("#1f3035"))
        painter.drawText(text_left, top + metrics.ascent() + 2,
                         metrics.elidedText(Path(path).name, Qt.ElideMiddle, text_width))
        if show_path:
            painter.setPen(QColor("#6b7c82"))
            painter.drawText(text_left, top + metrics.lineSpacing() + metrics.ascent() + 2,
                             metrics.elidedText(str(Path(path).parent), Qt.ElideMiddle, text_width))
    if len(paths) > len(shown):
        painter.setPen(QColor("#6b7c82"))
        painter.drawText(padding, height - padding + metrics.ascent() - gap,
                         f"……另有 {len(paths) - len(shown)} 个文件")
    painter.end()
    return image


def plain_text(mime):
    """优先取剪贴板纯文本；只有 HTML 时去掉标签与常见实体后再使用。"""
    text = (mime.text() or "").strip()
    if text:
        return text
    if not mime.hasHtml():
        return ""
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", mime.html())
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6])>", "\n", markup)
    markup = re.sub(r"<[^>]+>", " ", markup)
    text = unescape(markup).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t\f\v]+", " ", text).strip()


def read_clipboard(settings, clipboard=None):
    """按图片、文件、颜色、文字的顺序识别剪贴板内容。"""
    logger = get_logger()
    board = clipboard or QGuiApplication.clipboard()
    mime = board.mimeData()
    if mime is None:
        logger.debug("剪贴板没有可用的 MIME 数据")
        return None
    logger.debug("剪贴板格式: %s", mime.formats())
    if mime.hasImage():
        image = board.image()
        if not image.isNull():
            logger.debug("剪贴板识别为图片: %dx%d", image.width(), image.height())
            return ClipboardSource("image", image)
        logger.debug("剪贴板声明含图片但读取为空，继续按其他类型识别")
    if mime.hasUrls():
        paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        paths = [path for path in paths if path and os.path.exists(path)]
        if paths:
            logger.debug("剪贴板识别为文件: %d 个 %s", len(paths), paths[:3])
            return ClipboardSource("files", render_file_card(paths, settings), paths=paths)
        logger.debug("剪贴板含 URL 但没有本地存在的文件，继续按文本识别")
    text = plain_text(mime)
    # 颜色**优先于 HTML**（2026-10-11 修复）：从浏览器/很多工具复制颜色时剪贴板同时带 HTML，
    # 原先先判 HTML ⇒ 颜色分支永远到不了，颜色贴图退化成"默认绿底文字卡片"。
    if text and settings.get("clipboard_color_detection", True):
        color = parse_color(text)
        if color:
            logger.debug("剪贴板识别为颜色（优先于 HTML）: %s", color)
            return ClipboardSource("color", render_color_card(color, settings), text=color)
    if mime.hasHtml():
        html = mime.html()
        if html.strip() and text:
            return ClipboardSource("html", render_html_card(html, text, settings),
                                   text=text, html=html)
    if not text:
        logger.debug("剪贴板没有可用文本: board.text()=%r 格式=%s", board.text(), mime.formats())
        return None
    logger.debug("剪贴板识别为文字: 长度=%d 开头=%r", len(text), text[:40])
    return ClipboardSource("text", render_text_card(text, settings), text=text)
