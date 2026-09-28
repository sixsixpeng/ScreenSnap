"""贴图吸附的纯计算，不触碰系统 API，便于离屏测试。"""


def nearest(candidates, threshold):
    """挑绝对值最小的偏移；绝对值相同时优先窗口目标，避免最大化窗口总被屏幕抢走。

    candidates 为 (偏移, (key, edge), 优先级) 三元组，优先级越大越优先。
    """
    best = None
    for offset, hit, priority in candidates:
        if best is None or abs(offset) < abs(best[0]) or (
                abs(offset) == abs(best[0]) and priority > best[2]):
            best = (offset, hit, priority)
    if best is None or abs(best[0]) > threshold:
        return 0, None
    return best[0], best[1]


def axis_candidates(rect, targets):
    """分别返回水平与垂直方向的偏移候选，供吸附计算与诊断日志共用。

    每个候选为 (偏移, (key, edge), 优先级)。
    """
    horizontal = []
    vertical = []
    for target in targets:
        key = target["key"]
        priority = target.get("priority", 0)
        horizontal += [(target["left"] - rect[0], (key, "left"), priority),
                       (target["right"] - rect[2], (key, "right"), priority)]
        vertical += [(target["top"] - rect[1], (key, "top"), priority),
                     (target["bottom"] - rect[3], (key, "bottom"), priority)]
        if target.get("allow_outside"):
            # 贴到目标外侧：贴图左沿贴目标右沿，或贴图上沿贴目标下沿等。
            horizontal += [(target["right"] - rect[0], (key, "right"), priority),
                           (target["left"] - rect[2], (key, "left"), priority)]
            vertical += [(target["bottom"] - rect[1], (key, "bottom"), priority),
                         (target["top"] - rect[3], (key, "top"), priority)]
    return horizontal, vertical


def snap_offsets(rect, targets, threshold):
    """计算把贴图 rect 吸附到最近目标所需的位移。

    rect 为 (left, top, right, bottom)；targets 为包含 left/top/right/bottom/key
    的映射，right/bottom 用开区间（不含右/下边界）。allow_outside 为真时还允许
    贴到目标外侧，priority 用于同距离时挑选更想要的目标。
    返回 (dx, dy, hit)，hit 为 (key, edge)，两个偏移都为 0 时 hit 为 None。
    """
    horizontal, vertical = axis_candidates(rect, targets)
    dx, hit_x = nearest(horizontal, threshold)
    dy, hit_y = nearest(vertical, threshold)
    return dx, dy, hit_x or hit_y
