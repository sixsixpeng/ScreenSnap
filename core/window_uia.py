"""Windows UI Automation 元素识别（可选识别源）。

uiautomation 是纯 Python 的 UIA 封装，能读到浏览器网页控件、WPF、Qt、Electron
等自绘界面内部的控件；没有安装该库或识别失败时，调用方会自动退回窗口句柄识别。
"""

import ctypes
import logging
import os
import time
from ctypes import wintypes

from core.window_snap import top_window_at

# 自绘界面里过小的控件没有识别价值；4 像素仍能选中图标这类小控件。
MIN_SIZE = 4
# 兜底下钻时的上限：网页的 UIA 树动辄上万个节点，不设上限会拖垮查询。
VISIT_BUDGET = 60
DESCEND_LIMIT = 8
# 沿父链上溯或下钻时最多走多少层，防止异常树结构把查询拖住。
PARENT_LIMIT = 16
# 单次查询超过这个秒数就熔断，本进程内不再使用 UIA。
SLOW_SECONDS = 0.4
# 这些控件类型视为“容器”，命中后继续向里钻以找出真正可点的子项（列表项、树节点、
# 分组里的按钮等）；其余类型（按钮、文本框、图标、列表项本身）当作原子目标，命中即
# 止，避免一路钻到按钮里的 20x20 小图标这类无意义层级。ControlFromPoint 对虚拟化列表
# 往往只给到列表容器，靠这套规则才能把具体某项也识别出来。
CONTAINER_TYPES = frozenset({
    "PaneControl", "GroupControl", "ListControl", "WindowControl",
    "CustomControl", "DocumentControl", "ToolBarControl", "MenuControl",
    "MenuBarControl", "TreeControl", "TreeItemControl", "TabControl",
    "TabItemControl", "TableControl", "DataGridControl",
})
# 让某个窗口临时对命中测试“穿透”，这样 UIA 的 ControlFromPoint 能越过置顶的
# 截图遮罩，命中它下面的真实窗口；用完必须还原，否则遮罩收不到鼠标事件。
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
_warned = False
_disabled = False


def set_click_through(hwnd):
    """临时把窗口设为命中测试穿透，返回还原函数；失败返回 None。"""
    if not hwnd:
        return None
    user32 = ctypes.windll.user32
    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT)
    except (OSError, ValueError):
        return None

    def restore():
        try:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except (OSError, ValueError):
            pass

    return restore


def physical_rect_of(handle):
    """用 Win32 取窗口的物理矩形；UIA 对部分窗口取不到 BoundingRectangle 时兜底用。"""
    if not handle:
        return None
    user32 = ctypes.windll.user32
    bounds = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(bounds)):
        return None
    rect = (bounds.left, bounds.top, bounds.right, bounds.bottom)
    if rect[2] - rect[0] < MIN_SIZE or rect[3] - rect[1] < MIN_SIZE:
        return None
    return rect


def module():
    """按需导入 uiautomation；未安装时返回 None，不影响其他功能。"""
    global _warned
    if os.name != "nt":
        return None
    try:
        import uiautomation
        return uiautomation
    except (ImportError, OSError):
        if not _warned:
            _warned = True
            logging.getLogger("screensnap").warning(
                "未安装 uiautomation 库，UIA 识别不可用，已退回窗口句柄识别；"
                "需要时用 pip install uiautomation 安装")
        return None


def available():
    """设置页与日志用它提示 UIA 是否可用。"""
    return module() is not None


def element_chain(point, max_depth=3, exclude_hwnd=None):
    """返回覆盖该点的 UIA 控件矩形链（由外到内）；不可用时返回空列表。

    出错或过慢都会熔断，避免界面被无障碍查询拖死。
    exclude_hwnd 是置顶的截图遮罩句柄：查询瞬间让它命中穿透，UIA 才能越过它
    命中下面的真实窗口（ControlFromPoint 没有 Z 序概念，会打在遮罩上）。
    """
    global _disabled
    if _disabled:
        return []
    automation = module()
    if automation is None:
        logging.getLogger("screensnap").debug("未安装 uiautomation，跳过 UIA 识别")
        return []
    logger = logging.getLogger("screensnap")
    started = time.monotonic()
    try:
        return query(automation, point, int(max_depth), logger, exclude_hwnd)
    finally:
        cost = time.monotonic() - started
        if cost > SLOW_SECONDS:
            _disabled = True
            logger.warning("UIA 查询耗时 %.2f 秒，已停止使用 UIA，退回窗口句柄识别", cost)


def query(automation, point, max_depth, logger, exclude_hwnd=None):
    """真正执行一次 UIA 查询，调用方负责计时与异常兜底。"""
    global _disabled
    x, y = int(point[0]), int(point[1])
    try:
        # 鼠标下最上面的一定是本进程的遮罩，而 UIA 是树结构没有 Z 序概念，
        # 所以先用 Win32 找到遮罩下面的外部窗口，识别结果必须落在它里面。
        handle = top_window_at(x, y)
        if not handle:
            logger.debug("UIA 之前未取到外部窗口: 物理点(%d,%d)", x, y)
            return []
        win_rect = physical_rect_of(handle)
        control = control_at(automation, x, y, handle, logger, exclude_hwnd)
        if control is None:
            return []
        chain = climb(control, handle, max_depth, logger, win_rect)
        logger.debug("UIA 识别到 %d 层元素: %s", len(chain), chain)
        return chain
    except Exception as error:
        # UIA 调用偶发失败，交回句柄识别，不让识别功能整体失效。
        _disabled = True
        logger.warning("UIA 识别失败并已停用，退回窗口句柄识别: %s", error)
        return []


def control_at(automation, x, y, handle, logger, exclude_hwnd=None):
    """取该点最深的控件：原生命中后继续下钻到容器里的具体项，失败再退回下钻兜底。

    ControlFromPoint 已能命中大多数自绘界面的最里层（按钮、地址栏、网页焦点等），
    但对虚拟化列表（资源管理器/开始菜单的列表、树）往往只给到“列表”容器——所以
    命中成功后仍沿它的子树继续向下钻，把具体的列表项/树节点也找出来。只有当原生命
    中完全失败（比如打在本进程遮罩上）时，才退回 ControlFromHandle + 下钻兜底。
    """
    hit = control_from_point(automation, x, y, logger, exclude_hwnd)
    if hit is not None:
        logger.debug("UIA 原生命中最深控件: 名称=%r 类型=%r",
                     name_of(hit), type_of(hit))
        return deepest_at(hit, x, y, logger)
    try:
        control = automation.ControlFromHandle(handle)
    except Exception as error:
        logger.debug("UIA 取窗口 %d 的控件失败: %s", handle, error)
        return None
    if control is None:
        logger.debug("UIA 取不到窗口 %d 的控件", handle)
        return None
    logger.debug("UIA 从窗口 %d 开始下钻: 名称=%r 类型=%r",
                 handle, name_of(control), type_of(control))
    return deepest_at(control, x, y, logger)


def control_from_point(automation, x, y, logger, exclude_hwnd=None):
    """用 UIA 自己的命中测试取最深控件；命中本进程窗口（遮罩）时返回 None。

    exclude_hwnd 是置顶遮罩句柄：查询瞬间让它命中穿透，这样 ControlFromPoint
    能越过它命中下面的真实窗口，而不是永远打在本进程的遮罩上。
    """
    method = getattr(automation, "ControlFromPoint", None)
    if method is None:
        return None
    restore = set_click_through(exclude_hwnd)
    try:
        try:
            control = method(x, y)
        except Exception as error:
            logger.debug("UIA 命中测试失败: %s", error)
            return None
    finally:
        if restore is not None:
            restore()
    if control is None:
        return None
    rectangle = rectangle_of(control)
    if rectangle is None or not covers(rectangle, x, y):
        logger.debug("UIA 命中测试没有可用结果: 名称=%r 类型=%r 矩形=%s",
                     name_of(control), type_of(control), rectangle)
        return None
    if own_control(control):
        # 遮罩、贴图、设置窗口都是本进程的，必须让位给它们下面的外部窗口。
        logger.debug("UIA 命中测试命中本进程窗口，改用外部窗口下钻: 名称=%r 类型=%r",
                     name_of(control), type_of(control))
        return None
    logger.debug("UIA 命中控件: 名称=%r 类型=%r 矩形=%s",
                 name_of(control), type_of(control), rectangle)
    return control


def own_control(control):
    """命中的控件是否属于本进程（遮罩、贴图、设置窗口）。"""
    current = control
    for _ in range(PARENT_LIMIT):
        if current is None:
            return False
        handle = handle_of(current)
        if handle:
            return process_of(handle) == os.getpid()
        current = parent_of(current)
    return False


def climb(control, handle, max_depth, logger, win_rect=None):
    """从最深控件往上收集矩形，遇到目标窗口层为止。

    不再把“桌面”根算进来，否则它会白占一层，导致最里面的控件被截掉。
    win_rect 是 Win32 取到的窗口物理矩形；UIA 对某些窗口取不到
    BoundingRectangle（矩形为 None）时，用它兜底，保证窗口层不丢。
    """
    chain = []
    current = control
    for _ in range(PARENT_LIMIT):
        if current is None or len(chain) >= max_depth:
            break
        rectangle = rectangle_of(current)
        if rectangle is None and handle_of(current) == handle and win_rect is not None:
            rectangle = win_rect
        if rectangle is None:
            logger.debug("UIA 跳过元素: 名称=%r 类型=%r（矩形无效或小于 %d 像素）",
                         name_of(current), type_of(current), MIN_SIZE)
        elif rectangle in chain:
            logger.debug("UIA 跳过元素: 名称=%r 类型=%r 矩形=%s（与已有层重复）",
                         name_of(current), type_of(current), rectangle)
        else:
            chain.append(rectangle)
            logger.debug("UIA 元素[%d]: 名称=%r 类型=%r 矩形=%s",
                         len(chain), name_of(current), type_of(current), rectangle)
        if handle_of(current) == handle:
            break
        current = parent_of(current)
    chain.reverse()
    return chain


def deepest_at(control, x, y, logger, limit=DESCEND_LIMIT):
    """向下找覆盖该点的最深“可选项”控件。

    ControlFromPoint 对虚拟化列表常常只给到列表容器，这里沿它的子树继续下钻：
    只穿过“容器”类型（面板/分组/列表/树/工具栏…），一旦命中到非容器的原子控件
    （列表项、按钮、文本框、图标等）就停下来返回它——既把资源管理器里的单个文件、
    树里的某个节点找出来，又不会一路钻到按钮内部 20x20 的小图标。
    """
    current = control
    for depth in range(limit):
        if type_of(current) not in CONTAINER_TYPES:
            return current
        found = None
        for child in direct_children(current, logger):
            rect = rectangle_of(child)
            if rect is not None and covers(rect, x, y):
                found = child
                break
        if found is None:
            logger.debug("UIA 下钻 %d 层后没有更小的控件，停在 名称=%r 类型=%r",
                         depth, name_of(current), type_of(current))
            break
        if type_of(found) not in CONTAINER_TYPES:
            return found
        current = found
    return current


def direct_children(control, logger):
    """取控件全部直接子控件；任一环节失败都返回空列表，不让下钻拖垮识别。

    用 GetChildren 一次性取全，比逐个 GetFirst/NextSibling 更稳，能避免某些窗口
    首子控件取不到、导致下钻一上来就失败（只拿到窗口本身）的问题。
    """
    try:
        children = control.GetChildren()
    except Exception as error:
        logger.debug("UIA 读取子控件失败: 名称=%r 类型=%r %s",
                     name_of(control), type_of(control), error)
        return []
    if not isinstance(children, (list, tuple)):
        return []
    return children


def rectangle_of(control):
    """读取控件矩形 (left, top, right, bottom)；无效或过小时返回 None。

    uiautomation 的 BoundingRectangle 返回的是 Rect 对象（属性 left/top/right/
    bottom），不能直接当四元组解包，否则会抛 TypeError 导致这里永远返回 None、
    所有小控件都被当成“矩形无效”跳过。必须按属性取出。
    """
    try:
        rect = control.BoundingRectangle
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        if right - left >= MIN_SIZE and bottom - top >= MIN_SIZE:
            return (int(left), int(top), int(right), int(bottom))
    except Exception:
        pass
    return None


def handle_of(control):
    try:
        return int(control.NativeWindowHandle or 0)
    except Exception:
        return 0


def process_of(handle):
    """窗口所属进程；取不到时返回 0，避免误判成本进程。"""
    identifier = wintypes.DWORD()
    try:
        ctypes.windll.user32.GetWindowThreadProcessId(int(handle), ctypes.byref(identifier))
    except (OSError, ValueError):
        return 0
    return identifier.value


def covers(rectangle, x, y):
    left, top, right, bottom = rectangle
    return left <= x <= right and top <= y <= bottom


def parent_of(control):
    """上一级控件；取不到或到顶时返回 None。"""
    try:
        return control.GetParentControl()
    except Exception:
        return None


def name_of(control):
    try:
        return control.Name or ""
    except Exception:
        return ""


def type_of(control):
    try:
        return control.ControlTypeName
    except Exception:
        return ""
