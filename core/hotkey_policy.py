"""纯数据的热键偏好契约；配置、设置页与运行时共用，不构造Qt对象。"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Preference:
    key: str
    default: Any
    page: str
    group: str
    label: str
    help: str
    low: int = 0
    high: int = 0
    choices: tuple = ()


def option(key, default, page, group, label, help_text, low=0, high=0, choices=()):
    return Preference(key, default, page, group, label, help_text, low, high, tuple(choices))


H = "health"
D = "diagnostics"
C = "compatibility"
PREFERENCES = (
    option("hotkey_health_enabled", True, H, "状态与体检", "自动体检", "关闭后不自动判定或恢复；手动检查、诊断汇总仍可用。"),
    option("hotkey_health_interval", 4, H, "状态与体检", "体检周期(秒)", "2–600秒；没有异常证据时不重建绑定。", 2, 600),
    option("hotkey_startup_grace", 5, H, "状态与体检", "启动宽限(秒)", "启动后暂不自动恢复，让观察器完成初始化。", 0, 60),
    option("hotkey_wake_grace", 5, H, "状态与体检", "解锁/唤醒宽限(秒)", "恢复会话后丢弃旧输入证据，等待新的输入；长计时空档也会进入宽限。", 0, 60),
    option("hotkey_status_visible", True, H, "状态与体检", "显示状态面板", "状态只表明观察证据，不承诺所有热键或原生钩子正常。"),
    option("hotkey_recovery_enabled", True, H, "自动恢复", "异常时自动重建绑定", "只重建本程序拥有的绑定，不重启原生钩子；判定到卡住的按键记录时会一并清理。"),
    option("hotkey_diagnostic_only", False, H, "自动恢复", "只记录异常", "暂时覆盖自动恢复为关闭，不改写保存的自动恢复偏好；手动恢复仍可用。"),
    option("hotkey_retry_mode", "fixed", H, "自动恢复", "尝试间隔策略", "固定间隔或逐次延长；手动操作另受手动策略控制。", choices=(("固定", "fixed"), ("递增退避", "backoff"))),
    option("hotkey_retry_interval", 30, H, "自动恢复", "固定/初始间隔(秒)", "自动尝试至少间隔30秒，避免反复注销注册造成空窗。", 30, 600),
    option("hotkey_retry_max_interval", 300, H, "自动恢复", "最大退避间隔(秒)", "递增退避的上限，不得小于初始间隔；固定模式不使用。", 30, 3600),
    option("hotkey_retry_limit", 0, H, "自动恢复", "每次故障尝试上限", "0为不限；次数在工作线程实际开始时累计，未按键只记尚未验证。", choices=(("不限", 0), ("1次", 1), ("3次", 3), ("5次", 5))),
    option("hotkey_retry_exhausted", "stop", H, "自动恢复", "达到上限后", "停止自动尝试或冷却后允许下一批；有新异常证据才会再次尝试。", choices=(("停止", "stop"), ("冷却后再试", "cooldown"))),
    option("hotkey_retry_cooldown", 300, H, "自动恢复", "批次冷却(秒)", "达到有限次数上限后等待的时间，不是原生钩子恢复期限。", 30, 3600),
    option("hotkey_manual_bypass", True, H, "自动恢复", "手动恢复绕过冷却", "手动恢复可以立即尝试；关闭时也遵守当前间隔/停止策略。"),
    option("hotkey_manual_reset", True, H, "自动恢复", "手动恢复重置批次", "只重置策略计数与冷却；卡住的按键记录由恢复过程单独清理。"),
    option("hotkey_defer_busy", True, H, "自动恢复", "截图/编辑时延后自动恢复", "包括已排队的截图及两个编辑器；用户手动恢复不受此限制。"),
    option("hotkey_install_timeout", 5, H, "自动恢复", "注册停滞告警(秒)", "只报告注册长时间未完成，不终止线程或启动竞争安装。", 1, 60),
    option("hotkey_failure_notice", "first", H, "失败与恢复提示", "异常提示", "不提示、每次故障首次提示，或按间隔重复；不逐绑定刷通知。", choices=(("不提示", "off"), ("首次", "first"), ("重复", "repeat"))),
    option("hotkey_notice_interval", 300, H, "失败与恢复提示", "重复提示间隔(秒)", "同一故障至少间隔这么久，声音/普通操作提示仍遵守已有总开关。", 30, 3600),
    option("hotkey_recovered_notice", True, H, "失败与恢复提示", "故障后回调恢复提示", "仅当故障涉及的动作实际到达Qt线程后提示，不把注册完成当恢复。"),
    option("hotkey_criterion_threads", True, H, "高级判定", "检查库线程", "实际监听/处理线程不可用属于异常，无法读取的线程状态保持未知。"),
    option("hotkey_criterion_misses", True, H, "高级判定", "检查组合键无回调", "要求独立按下/释放、修饰键精确匹配，长按自动重复不累计。"),
    option("hotkey_criterion_input", True, H, "高级判定", "检查输入观察滞后", "GetLastInputInfo包括键鼠，滞后不是系统摘钩证明。"),
    option("hotkey_criterion_phantom", True, H, "高级判定", "检查卡住的按键记录",
           "把记录的「仍按下」与系统实际按键状态核对；存在只记录了按下、系统显示未按下的残留键时判为异常并清理"
           "（残留键会让所有热键都不响应）。关闭后不再自动核对与清理，仍可用「重新注册」手动清理。"),
    option("hotkey_sensitivity", "standard", H, "高级判定", "灵敏度", "预设只计算有效参数，不覆盖自定义保存值。", choices=(("保守", "conservative"), ("标准", "standard"), ("快速", "fast"), ("自定义", "custom"))),
    option("hotkey_miss_count", 2, H, "高级判定", "连续未触发次数", "独立尝试数量，达到后仍需等待回调宽限结束。", 2, 5),
    option("hotkey_miss_window", 10, H, "高级判定", "证据窗口(秒)", "旧证据按时间过期；轮询不延长证据寿命。", 5, 60),
    option("hotkey_recent_input_ms", 1500, H, "高级判定", "系统近期输入(毫秒)", "系统输入比现在更近于此值时才检查观察滞后。", 500, 5000),
    option("hotkey_missed_input_ms", 2500, H, "高级判定", "观察滞后阈值(毫秒)", "必须大于近期输入窗口，避免把正常调度当异常。", 1000, 10000),
    option("hotkey_callback_grace_ms", 250, H, "高级判定", "回调顺序宽限(毫秒)", "同时兼容观察器先到及热键回调先到；钩子线程只更新元数据。", 100, 1000),
    option("hotkey_diagnostic_level", "standard", D, "热键诊断", "额外诊断", "关闭额外日志不关闭健康判定；详细诊断仅含元数据，不记录输入内容。", choices=(("关闭", "off"), ("仅异常", "errors"), ("标准", "standard"), ("详细", "detailed"))),
    option("hotkey_summary_enabled", True, D, "热键诊断", "周期状态汇总", "独立于体检和自动恢复的计时器；不逐键输出。"),
    option("hotkey_summary_interval", 600, D, "热键诊断", "汇总间隔(秒)", "汇总不因体检关闭而停止；受普通日志总开关和额外诊断级别控制。", 60, 3600),
    option("hotkey_trace_interval", 2, D, "热键诊断", "回调详情节流(秒)", "仅限制同动作详细日志，不限制热键动作。", 1, 60),
    option("hotkey_detail_minutes", 10, D, "热键诊断", "临时详细诊断(分钟)", "点击开始后自动结束；不改普通日志等级，设置刷新不会延长计时。", 1, 30),
    option("hotkey_snapshot_on_fault", True, D, "热键诊断", "异常时保留快照", "仅保存在内存，记录状态及计数，不存键名、窗口标题或用户路径。"),
    option("hotkey_snapshot_limit", 20, D, "热键诊断", "快照数量上限", "超过后淘汰最旧项；退出清空，不作为用户会话数据持久化。", 1, 100),
    option("hotkey_report_include_logs", False, D, "热键诊断", "导出时附带原始日志", "原始日志可能包含标题、路径和命令行；每次导出仍需明确确认。"),
    option("log_backup_count", 14, D, "日志记录", "轮转备份份数", "每个日志轮转组保留的份数，不是天数，也不清理旧月份目录。", 1, 100),
    option("capture_toolbar_hotkey_fallback", True, C, "键盘兼容性", "工具栏隐藏键全局兜底", "关闭仅停全局兜底，正常Qt操作仍可用；某些副屏可能收不到Qt按键。"),
    option("capture_quick_sticker_hotkey_fallback", True, C, "键盘兼容性", "快速贴图键全局兜底", "独立于快速贴图功能开关，永不吞Space原有画布操作。"),
    option("capture_escape_hotkey_fallback", True, C, "键盘兼容性", "Escape全局兜底", "关闭仅停兜底，正常Escape及鼠标/托盘操作不变；模态窗口优先接收Escape。"),
)
SPECS = {item.key: item for item in PREFERENCES}
DEFAULTS = {item.key: item.default for item in PREFERENCES}
THRESHOLD_KEYS = ("hotkey_miss_count", "hotkey_miss_window", "hotkey_recent_input_ms",
                  "hotkey_missed_input_ms", "hotkey_callback_grace_ms")
PRESETS = {
    "conservative": (3, 20, 1500, 5000, 500),
    "standard": (2, 10, 1500, 2500, 250),
    "fast": (2, 10, 1000, 2000, 150),
}


def normalize_key_name(name):
    """把按键名归一化成比较用的小写形式，并统一常见别名（esc/enter/delete/page up…）。

    只做字符串规范化，不查询任何输入状态——同一函数同时供配置校验与运行时事件匹配，
    保证“设置里能存的名字”和“钩子回调里的名字”用同一套规则比较。
    """
    text = " ".join(str(name).strip().lower().split())
    return {"escape": "esc", "return": "enter", "del": "delete",
            "pageup": "page up", "pagedown": "page down"}.get(text, text)


def shortcut_signature(binding):
    """单组合冲突判定；不支持的序列不猜测，更不查询共享输入状态。"""
    text = str(binding or "").strip()
    if not text or "," in text:
        return None
    aliases = {"control": "ctrl", "meta": "windows", "win": "windows", "cmd": "windows"}
    parts = [normalize_key_name(part) for part in text.split("+")]
    return frozenset(aliases.get(part, part) for part in parts) if all(parts) else None


def validate_preference(key, value):
    """校验单个策略键：类型必须与默认值一致；有候选集则必须在集内；整数须落在 [low, high]。

    与 `validate_relations` 分开：这里不看其它键，因此可以在 repair 中逐键独立修复，
    不会用“默认的另一半”否定一个本身合法的自定义值。
    """
    spec = SPECS[key]
    if type(value) is not type(spec.default):
        raise ValueError(f"配置项 {key} 类型错误")
    if spec.choices and value not in tuple(choice[1] for choice in spec.choices):
        raise ValueError(f"配置项 {key} 取值无效")
    if type(value) is int and not spec.choices and not spec.low <= value <= spec.high:
        raise ValueError(f"配置项 {key} 必须在 {spec.low}–{spec.high} 之间")
    return value


def validate_relations(values):
    """跨字段约束（逐键校验之后才可调用）：退避上限 ≥ 初始间隔；滞后阈值 > 近期输入窗口。"""
    if values["hotkey_retry_mode"] == "backoff" and values["hotkey_retry_max_interval"] < values["hotkey_retry_interval"]:
        raise ValueError("最大退避间隔不得小于初始间隔")
    if values["hotkey_sensitivity"] == "custom" and values["hotkey_missed_input_ms"] <= values["hotkey_recent_input_ms"]:
        raise ValueError("观察滞后阈值必须大于系统近期输入窗口")


def effective_policy(settings) -> dict[str, Any]:
    """不改保存值；私有/测试调用只给部分配置时也有安全默认。"""
    result = dict(DEFAULTS)
    for key in SPECS:
        try:
            result[key] = validate_preference(key, settings.get(key, DEFAULTS[key]))
        except ValueError:
            pass
    preset = result["hotkey_sensitivity"]
    if preset in PRESETS:
        result.update(zip(THRESHOLD_KEYS, PRESETS[preset]))
    result["hotkey_retry_max_interval"] = max(result["hotkey_retry_interval"], result["hotkey_retry_max_interval"])
    result["hotkey_missed_input_ms"] = max(result["hotkey_recent_input_ms"] + 1, result["hotkey_missed_input_ms"])
    result["recovery_effective"] = result["hotkey_health_enabled"] and result["hotkey_recovery_enabled"] and not result["hotkey_diagnostic_only"]
    return result


def preference_visible(key, settings):
    """表单与运行时共用同一有效策略，隐藏不删除用户值。"""
    p = effective_policy(settings)
    if SPECS[key].page == D and key in ("hotkey_diagnostic_level", "hotkey_summary_enabled",
            "hotkey_summary_interval", "hotkey_trace_interval", "hotkey_detail_minutes") and not settings.get("logging_enabled", True):
        return False
    health = p["hotkey_health_enabled"]
    recovery = p["recovery_effective"]
    if key in ("hotkey_health_interval", "hotkey_startup_grace", "hotkey_wake_grace"):
        return health
    if key.startswith("hotkey_criterion_") or key == "hotkey_sensitivity":
        return health
    if key in THRESHOLD_KEYS:
        criterion = "hotkey_criterion_input" if "input" in key else "hotkey_criterion_misses"
        return health and p["hotkey_sensitivity"] == "custom" and p[criterion]
    if key.startswith("hotkey_retry_") or key in ("hotkey_defer_busy", "hotkey_install_timeout"):
        if not recovery:
            return False
        if key == "hotkey_retry_max_interval":
            return p["hotkey_retry_mode"] == "backoff"
        if key in ("hotkey_retry_exhausted", "hotkey_retry_cooldown"):
            return p["hotkey_retry_limit"] > 0 and (key != "hotkey_retry_cooldown" or p["hotkey_retry_exhausted"] == "cooldown")
    if key == "hotkey_notice_interval":
        return p["hotkey_failure_notice"] == "repeat"
    if key == "hotkey_summary_interval":
        return p["hotkey_summary_enabled"] and p["hotkey_diagnostic_level"] in ("standard", "detailed")
    if key == "capture_quick_sticker_hotkey_fallback":
        return settings.get("capture_quick_sticker_enabled", True)
    return True
