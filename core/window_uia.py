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
from core.window_snap import ignored_mask_window

# 自绘界面里过小的控件没有识别价值；4 像素仍能选中图标这类小控件。
MIN_SIZE = 4
# 兜底下钻时的上限：网页的 UIA 树动辄上万个节点，不设上限会拖垮查询。
VISIT_BUDGET = 60
DESCEND_LIMIT = 24
# 沿父链上溯或下钻时最多走多少层，防止异常树结构把查询拖住。
PARENT_LIMIT = 32
# 单次下钻最多读多少个控件的属性：网页与虚拟化列表的子控件常有上千个，逐个跨进程读
# 矩形会让一次悬停查询卡几百毫秒。预算用尽就立即停止下钻，返回当前最优候选。
PROPERTY_READ_BUDGET = 180
# 单层最多枚举多少个直接子控件：用增量接口取到这个数就停，避免把上千个子控件
# 一次性实例化成 COM 对象。
CHILDREN_FETCH_LIMIT = 128
# 单次查询超过这个秒数就熔断，本进程内不再使用 UIA。
SLOW_SECONDS = 0.4
SLOW_COOLDOWN_SECONDS = 1.0
# 可由设置覆盖的三项性能参数（下钻读取预算 / 单层子控件上限 / 熔断阈值）；
# 进程内全局生效，遮罩打开时按当前设置刷新一次，见 mask_window._apply_uia_limits。
_limits = {"read_budget": PROPERTY_READ_BUDGET,
           "children_limit": CHILDREN_FETCH_LIMIT,
           "slow_seconds": SLOW_SECONDS}


def set_limits(read_budget=None, children_limit=None, slow_seconds=None):
    """按设置覆盖性能参数；传 None 表示保持当前值。"""
    if read_budget:
        _limits["read_budget"] = max(1, int(read_budget))
    if children_limit:
        _limits["children_limit"] = max(1, int(children_limit))
    if slow_seconds:
        _limits["slow_seconds"] = max(0.05, float(slow_seconds))
# 这些控件类型视为“容器”，命中后继续向里钻以找出真正可点的子项（列表项、树节点、
# 分组里的按钮等）；其余类型（按钮、文本框、图标、列表项本身）当作原子目标，命中即
# 止，避免一路钻到按钮里的 20x20 小图标这类无意义层级。ControlFromPoint 对虚拟化列表
# 往往只给到列表容器，靠这套规则才能把具体某项也识别出来。
CONTAINER_TYPES = frozenset({
    "PaneControl", "GroupControl", "ListControl", "WindowControl",
    "CustomControl", "DocumentControl", "ToolBarControl", "MenuControl",
    "MenuBarControl", "TreeControl", "TreeItemControl", "TabControl",
    "TabItemControl", "TableControl", "DataGridControl", "ListItemControl",
    "DataItemControl", "ComboBoxControl", "HeaderControl", "HeaderItemControl",
    "SplitButtonControl", "SpinnerControl", "SliderControl",
    "DateTimePickerControl", "CalendarControl", "DataGridRowControl",
})
# 让某个窗口临时对命中测试“穿透”，这样 UIA 的 ControlFromPoint 能越过置顶的
# 截图遮罩，命中它下面的真实窗口；用完必须还原，否则遮罩收不到鼠标事件。
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
_warned = False
_disabled_until = 0.0
_last_debug_signature = None

# ⚠ 不要把 UIA 查询挪到工作线程：查询里的 set_click_through 会对主线程创建的遮罩
# 窗口做跨线程 SetWindowLongW，而 SetWindowLong 会向窗口所属线程 SendMessage
# (WM_STYLECHANGING/WM_STYLECHANGED)；主线程一旦正在等查询结果就会死锁，加上遮罩是
# 覆盖全屏的置顶窗口，表现就是整个系统卡死。UIA 查询必须留在调用它的线程里同步执行，
# 由下面的 SLOW_SECONDS 熔断兜底。


# 按遮罩句柄缓存原始扩展样式：同一块遮罩一次截图里会被查询很多次，缓存后每次
# 查询只剩两次 SetWindowLongW。遮罩关闭时用 forget_click_through 释放，避免句柄
# 被系统回收复用后拿到过期样式。
_exstyle_cache = {}


def forget_click_through(hwnd):
    """遮罩关闭时清掉该句柄缓存的原始扩展样式。"""
    if hwnd:
        _exstyle_cache.pop(int(hwnd), None)


# 鼠标左/右/中键的虚拟键码。命中穿透会让遮罩短暂对点击透明，按下期间直接不做
# 切换，避免把用户点击透传给下面的窗口（此时 ControlFromPoint 会命中遮罩本身，
# 由 control_at 自动退回 ControlFromHandle + 下钻）。
MOUSE_BUTTON_KEYS = (0x01, 0x02, 0x04)


def mouse_button_down():
    """当前是否有鼠标按键按下；取不到状态时视为没有。"""
    try:
        user32 = ctypes.windll.user32
        return any(user32.GetAsyncKeyState(key) & 0x8000 for key in MOUSE_BUTTON_KEYS)
    except (OSError, ValueError):
        return False


def set_click_through(hwnd):
    """临时把窗口设为命中测试穿透，返回还原函数；失败或有鼠标按下时返回 None。"""
    if not hwnd or mouse_button_down():
        return None
    user32 = ctypes.windll.user32
    try:
        style = _exstyle_cache.get(int(hwnd))
        if style is None:
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            _exstyle_cache[int(hwnd)] = style
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
    """取窗口可见物理边界，优先排除 GetWindowRect 的不可见缩放边框。"""
    if not handle:
        return None
    bounds = wintypes.RECT()
    try:
        # DWMWA_EXTENDED_FRAME_BOUNDS is in physical pixels and excludes the
        # invisible resize border that can make maximized windows look cross-screen.
        dwm = ctypes.WinDLL("dwmapi")
        get_attribute = dwm.DwmGetWindowAttribute
        get_attribute.argtypes = (wintypes.HWND, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.RECT), wintypes.DWORD)
        get_attribute.restype = ctypes.c_long
        result = get_attribute(wintypes.HWND(handle), 9, ctypes.byref(bounds),
                               ctypes.sizeof(bounds))
        if result == 0:
            rect = (bounds.left, bounds.top, bounds.right, bounds.bottom)
            if rect[2] - rect[0] >= MIN_SIZE and rect[3] - rect[1] >= MIN_SIZE:
                return rect
    except (AttributeError, OSError, ValueError, ctypes.ArgumentError):
        pass
    bounds = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(bounds)):
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


class _PropertyCache:
    """单次查询内的控件属性缓存。

    读取 UIA 属性是跨进程调用，也是本模块的主要开销；同一次查询里同一个控件会被
    多次访问（下钻时入栈/出栈、向上收集层级等）。缓存后每个控件每个属性只读一次，
    结果与逐次读取完全一致，只是不再重复往返。
    """

    def __init__(self):
        self._items = {}

    def _props(self, control):
        # 用 id 作键并同时持有控件引用：防止控件在查询中途被回收后 id 复用串味。
        entry = self._items.get(id(control))
        if entry is None or entry[0] is not control:
            entry = (control, {})
            self._items[id(control)] = entry
        return entry[1]

    def rect(self, control):
        props = self._props(control)
        if "rect" not in props:
            props["rect"] = rectangle_of(control)
        return props["rect"]

    def type(self, control):
        props = self._props(control)
        if "type" not in props:
            props["type"] = type_of(control)
        return props["type"]

    def name(self, control):
        props = self._props(control)
        if "name" not in props:
            props["name"] = name_of(control)
        return props["name"]

    def handle(self, control):
        props = self._props(control)
        if "handle" not in props:
            props["handle"] = handle_of(control)
        return props["handle"]


def _rect_of(control, cache):
    return cache.rect(control) if cache is not None else rectangle_of(control)


def _type_of(control, cache):
    return cache.type(control) if cache is not None else type_of(control)


def _name_of(control, cache):
    return cache.name(control) if cache is not None else name_of(control)


def _handle_of(control, cache):
    return cache.handle(control) if cache is not None else handle_of(control)


def element_chain(point, max_depth=3, exclude_hwnd=None, debug_tree=False,
                  deepest_only=False):
    """返回覆盖该点的 UIA 控件矩形链（由外到内）；不可用时返回空列表。

    出错或过慢都会熔断，避免界面被无障碍查询拖死。这个函数（以及它内部的
    set_click_through）必须留在调用它的线程里同步执行，不能挪到工作线程——原因见文件头部说明。
    exclude_hwnd 是置顶的截图遮罩句柄：查询瞬间让它命中穿透，UIA 才能越过它
    命中下面的真实窗口（ControlFromPoint 没有 Z 序概念，会打在遮罩上）。
    deepest_only=True 时只返回最内层控件（悬停高亮用），跳过整套父链上溯。
    """
    global _disabled_until
    if time.monotonic() < _disabled_until:
        return []
    automation = module()
    if automation is None:
        logging.getLogger("screensnap").debug("未安装 uiautomation，跳过 UIA 识别")
        return []
    logger = logging.getLogger("screensnap")
    started = time.monotonic()
    try:
        return query(automation, point, int(max_depth), logger, exclude_hwnd, debug_tree,
                     deepest_only)
    finally:
        cost = time.monotonic() - started
        if cost > _limits["slow_seconds"]:
            _disabled_until = time.monotonic() + SLOW_COOLDOWN_SECONDS
            logger.warning("UIA 查询耗时 %.2f 秒，暂停 %.1f 秒后重试",
                           cost, SLOW_COOLDOWN_SECONDS)


def query(automation, point, max_depth, logger, exclude_hwnd=None, debug_tree=False,
          deepest_only=False):
    """真正执行一次 UIA 查询，调用方负责计时与异常兜底。"""
    global _disabled_until, _last_debug_signature
    x, y = int(point[0]), int(point[1])
    # 同一次查询里对同一控件的属性只读一次，避免反复跨进程取值。
    cache = _PropertyCache()
    try:
        # 鼠标下最上面的一定是本进程的遮罩，而 UIA 是树结构没有 Z 序概念，
        # 所以先用 Win32 找到遮罩下面的外部窗口，识别结果必须落在它里面。
        handle = top_window_at(x, y, skip_stickers=False)
        if not handle:
            logger.debug("UIA 之前未取到外部窗口: 物理点(%d,%d)", x, y)
            return []
        win_rect = physical_rect_of(handle)
        control = control_at(automation, x, y, handle, logger, exclude_hwnd, cache)
        if control is None:
            return []
        if debug_tree:
            # 结构诊断才需要这组属性；常规识别下取它纯属浪费跨进程调用。
            signature = (handle, _type_of(control, cache), _name_of(control, cache),
                         _rect_of(control, cache))
            if signature != _last_debug_signature:
                _last_debug_signature = signature
                logger.info("UIA 结构诊断（最多 60 个节点；不读取控件值）:\n%s",
                            format_control_tree(control, logger))
        else:
            _last_debug_signature = None
        if deepest_only:
            # 悬停只需要最内层：跳过父链上溯（element_depth 层，每层约两次跨进程读取）。
            rect = _rect_of(control, cache)
            if rect is None:
                return []
            if win_rect is not None:
                rect = _clamp_rect(rect, win_rect)
            return [rect]
        chain = climb(control, handle, max_depth, logger, win_rect, cache)
        logger.debug("UIA 识别到 %d 层元素: %s", len(chain), chain)
        return chain
    except Exception as error:
        # UIA 调用偶发失败，交回句柄识别，不让识别功能整体失效。
        _disabled_until = time.monotonic() + SLOW_COOLDOWN_SECONDS
        logger.warning("UIA 识别失败，暂停 %.1f 秒后重试并退回窗口句柄识别: %s",
                   SLOW_COOLDOWN_SECONDS, error)
        return []


def control_at(automation, x, y, handle, logger, exclude_hwnd=None, cache=None):
    """取该点最深的控件：原生命中后继续下钻到容器里的具体项，失败再退回下钻兜底。

    ControlFromPoint 已能命中大多数自绘界面的最里层（按钮、地址栏、网页焦点等），
    但对虚拟化列表（资源管理器/开始菜单的列表、树）往往只给到“列表”容器——所以
    命中成功后仍沿它的子树继续向下钻，把具体的列表项/树节点也找出来。只有当原生命
    中完全失败（比如打在本进程遮罩上）时，才退回 ControlFromHandle + 下钻兜底。
    """
    hit = control_from_point(automation, x, y, logger, exclude_hwnd, cache)
    if hit is not None:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("UIA 原生命中最深控件: 名称=%r 类型=%r",
                         _name_of(hit, cache), _type_of(hit, cache))
        return deepest_at(hit, x, y, logger, cache=cache)
    try:
        control = automation.ControlFromHandle(handle)
    except Exception as error:
        logger.debug("UIA 取窗口 %d 的控件失败: %s", handle, error)
        return None
    if control is None:
        logger.debug("UIA 取不到窗口 %d 的控件", handle)
        return None
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("UIA 从窗口 %d 开始下钻: 名称=%r 类型=%r",
                     handle, _name_of(control, cache), _type_of(control, cache))
    return deepest_at(control, x, y, logger, cache=cache)


def control_from_point(automation, x, y, logger, exclude_hwnd=None, cache=None):
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
    rectangle = _rect_of(control, cache)
    if rectangle is None or not covers(rectangle, x, y):
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("UIA 命中测试没有可用结果: 名称=%r 类型=%r 矩形=%s",
                         _name_of(control, cache), _type_of(control, cache), rectangle)
        return None
    if own_control(control, cache):
        # 只让位给截图遮罩和贴图；本程序普通窗口仍允许参与 UIA 识别。
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("UIA 命中测试命中本程序覆盖层，继续向下穿透: 名称=%r 类型=%r",
                         _name_of(control, cache), _type_of(control, cache))
        return None
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("UIA 命中控件: 名称=%r 类型=%r 矩形=%s",
                     _name_of(control, cache), _type_of(control, cache), rectangle)
    return control


def own_control(control, cache=None):
    """命中控件是否属于需穿透的截图遮罩。

    只有遮罩窗口（screensnap_mask）会被忽略；贴图、菜单等其它本程序窗口
    不再被排除，UIA 识别可以正常命中它们。
    """
    current = control
    for _ in range(PARENT_LIMIT):
        if current is None:
            return False
        handle = _handle_of(current, cache)
        if handle:
            pid = process_of(handle)
            # 已经上溯到其它进程的控件，就不可能是本程序遮罩的后代，直接结束。
            if pid and pid != os.getpid():
                return False
            if pid == os.getpid() and ignored_mask_window(handle):
                return True
        current = parent_of(current)
    return False


def climb(control, handle, max_depth, logger, win_rect=None, cache=None):
    """从最深控件往上收集矩形，遇到目标窗口层为止。

    不再把“桌面”根算进来，否则它会白占一层，导致最里面的控件被截掉。
    win_rect 是 Win32 取到的窗口物理矩形；UIA 对某些窗口取不到
    BoundingRectangle（矩形为 None）时，用它兜底，保证窗口层不丢。
    """
    chain = []
    current = control
    # 名称与类型只用于日志；DEBUG 关闭时不再为它们付出跨进程读取的代价。
    verbose = logger.isEnabledFor(logging.DEBUG)
    for _ in range(PARENT_LIMIT):
        if current is None or len(chain) >= max_depth:
            break
        rectangle = _rect_of(current, cache)
        current_handle = _handle_of(current, cache)
        if win_rect is not None:
            if current_handle == handle:
                # The native visible frame is more reliable than UIA bounds for maximized roots.
                rectangle = win_rect
            elif rectangle is not None:
                # 子控件矩形也可能比窗口可见框大（虚拟滚动区、被 DPI 虚拟化、最大化窗口的
                # 不可见 resize frame 会渗进子层），不收边会被当成跨屏选区而误进独立编辑器。
                rectangle = _clamp_rect(rectangle, win_rect)
        if rectangle is None:
            if verbose:
                logger.debug("UIA 跳过元素: 名称=%r 类型=%r（矩形无效或小于 %d 像素）",
                             _name_of(current, cache), _type_of(current, cache), MIN_SIZE)
        elif rectangle in chain:
            if verbose:
                logger.debug("UIA 跳过元素: 名称=%r 类型=%r 矩形=%s（与已有层重复）",
                             _name_of(current, cache), _type_of(current, cache), rectangle)
        else:
            chain.append(rectangle)
            if verbose:
                logger.debug("UIA 元素[%d]: 名称=%r 类型=%r 矩形=%s",
                             len(chain), _name_of(current, cache),
                             _type_of(current, cache), rectangle)
        if current_handle == handle:
            break
        current = parent_of(current)
    chain.reverse()
    return chain


def deepest_at(control, x, y, logger, limit=DESCEND_LIMIT, cache=None,
               read_budget=None):
    """向下找覆盖该点的最深“可选项”控件。

    ControlFromPoint 对虚拟化列表常常只给到列表容器，这里沿它的子树继续下钻：
    只穿过“容器”类型（面板/分组/列表/树/工具栏…），一旦命中到非容器的原子控件
    （列表项、按钮、文本框、图标等）就停下来返回它——既把资源管理器里的单个文件、
    树里的某个节点找出来，又不会一路钻到按钮内部 20x20 的小图标。

    读取 UIA 属性是跨进程调用，网页/虚拟化列表的子控件可能上千个；这里用一个总读取
    预算兜底，预算用尽立即返回当前最优候选，保证单次悬停查询的耗时上限可控。
    """
    read_budget = read_budget or _limits["read_budget"]
    candidates = []
    pending = [(control, 0)]
    visited = set()
    reads = [read_budget]

    def read_rect(target):
        reads[0] -= 1
        return _rect_of(target, cache)

    while pending and len(visited) < VISIT_BUDGET and reads[0] > 0:
        current, depth = pending.pop()
        identity = id(current)
        if identity in visited:
            continue
        visited.add(identity)
        rect = read_rect(current)
        if rect is None or not covers(rect, x, y):
            continue
        area = (rect[2] - rect[0]) * (rect[3] - rect[1])
        is_container = (_type_of(current, cache) in CONTAINER_TYPES) if reads[0] > 0 else False
        reads[0] -= 1
        # 名称只用于“同尺寸时优先取有名者”的兜底，这里先不读，留到决出并列者再取。
        candidates.append(((area, is_container, -depth), current))
        if depth >= limit or not is_container:
            continue
        children = direct_children(current, logger)
        for child in reversed(children):
            if reads[0] <= 0:
                break
            child_rect = read_rect(child)
            if child_rect is not None and covers(child_rect, x, y):
                pending.append((child, depth + 1))

    if pending and reads[0] <= 0:
        logger.debug("UIA 下钻达到读取预算 %d，未检查剩余分支", read_budget)
    elif pending:
        logger.debug("UIA 下钻达到节点预算 %d，未检查剩余分支", VISIT_BUDGET)
    if not candidates:
        return control
    # 与原先按 (面积, 是否容器, 有无名称, 层级) 取最小等价：先选出面积/容器/深度的
    # 最小者，再从并列者中取第一个有名称的；都无名称则取第一个。只有唯一候选时
    # 连名称都不用读。
    best_key = min(candidate[0] for candidate in candidates)
    tied = [candidate for candidate in candidates if candidate[0] == best_key]
    if len(tied) == 1:
        return tied[0][1]
    for _, candidate in tied:
        if _name_of(candidate, cache):
            return candidate
    return tied[0][1]


def direct_children(control, logger, limit=None):
    """取控件直接子控件，最多 limit 个；任一环节失败都返回空列表。

    优先用 GetFirstChildControl/GetNextSiblingControl 增量枚举，取够 limit 就停，
    避免一次性把上千个子控件都实例化成 COM 对象；增量接口取不到时退回 GetChildren
    （取到全量后再截断），保持旧行为能拿到首子控件的稳定性。
    """
    limit = limit or _limits["children_limit"]
    first = getattr(control, "GetFirstChildControl", None)
    if first is not None:
        children = []
        try:
            child = first()
            while child is not None and len(children) < limit:
                children.append(child)
                # 下一个兄弟要在**子控件**上取（与 uiautomation 的 GetChildren 一致）；
                # 取到父控件的兄弟会把无关控件当成子控件，悬停会高亮错元素。
                child = child.GetNextSiblingControl()
        except Exception as error:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("UIA 增量读取子控件失败: 名称=%r 类型=%r %s",
                             name_of(control), type_of(control), error)
            if children:
                return children
        else:
            if children:
                return children
    try:
        children = control.GetChildren()
    except Exception as error:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("UIA 读取子控件失败: 名称=%r 类型=%r %s",
                         name_of(control), type_of(control), error)
        return []
    if not isinstance(children, (list, tuple)):
        return []
    return list(children)[:limit]


def rectangle_of(control):
    """读取控件矩形 (left, top, right, bottom)；无效或过小时返回 None。

    uiautomation 的 BoundingRectangle 返回的是 Rect 对象（属性 left/top/right/
    bottom），不能直接当四元组解包，否则会抛 TypeError 导致这里永远返回 None、
    所有小控件都被当成“矩形无效”跳过。必须按属性取出。
    """
    try:
        rect = control.BoundingRectangle
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        if right > left and bottom > top:
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


def _clamp_rect(rectangle, frame):
    """把控件矩形收边到窗口可见框内；完全落在框外或收成过小时保留原矩形（多为弹出层）。"""
    left = max(rectangle[0], frame[0])
    top = max(rectangle[1], frame[1])
    right = min(rectangle[2], frame[2])
    bottom = min(rectangle[3], frame[3])
    if right - left < MIN_SIZE or bottom - top < MIN_SIZE:
        return rectangle
    return (left, top, right, bottom)


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


def format_control_tree(control, logger, node_limit=60, max_depth=8):
    """格式化有限的目标祖先链和相邻子树，不读取 UIA Value。"""
    ancestors = []
    current = control
    for _ in range(min(PARENT_LIMIT, node_limit)):
        if current is None:
            break
        ancestors.append(current)
        current = parent_of(current)

    lines = []
    for depth, item in enumerate(reversed(ancestors)):
        name = name_of(item).replace("\r", " ").replace("\n", " ")[:120]
        lines.append(f"{'  ' * depth}- {type_of(item)} name={name!r} rect={rectangle_of(item)}")

    if len(ancestors) > 1:
        parent = ancestors[1]
        lines.append("  父级子树（命中分支优先，最多 60 个节点）:")
        remaining = [max(0, node_limit - len(ancestors))]
        seen = {id(parent)}
        target_signature = (handle_of(control), type_of(control), name_of(control),
                            rectangle_of(control))

        def is_target(item):
            return (item is control or
                    (handle_of(item), type_of(item), name_of(item), rectangle_of(item)) ==
                    target_signature)

        def append_children(item, depth):
            if depth >= max_depth or remaining[0] <= 0:
                return
            children = direct_children(item, logger)
            preferred = next((child for child in children if child is control), None)
            if preferred is None:
                preferred = next((child for child in children if is_target(child)), None)
            if preferred is not None:
                children = [preferred] + [child for child in children if child is not preferred]
            for child in children:
                if remaining[0] <= 0:
                    break
                if id(child) in seen:
                    continue
                seen.add(id(child))
                name = name_of(child).replace("\r", " ").replace("\n", " ")[:120]
                marker = ">" if is_target(child) else "-"
                lines.append(f"    {'  ' * depth}{marker} {type_of(child)} "
                             f"name={name!r} rect={rectangle_of(child)}")
                remaining[0] -= 1
                append_children(child, depth + 1)

        append_children(parent, 0)
    return "\n".join(lines)
