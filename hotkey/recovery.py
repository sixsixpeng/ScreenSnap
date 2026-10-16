"""恢复策略只管绑定重建，不操作keyboard的原生监听器或按键表。

所有字段都在同一把可重入锁下读写：工作线程（安装/恢复）与 Qt 线程（体检、诊断、
设置刷新）会并发调用，门禁判定与计数必须原子，否则会重复计数或放行两次尝试。
`state` 只用于展示与判定，不代表底层原生钩子或库线程的真实健康。

日志只写在**低频边界**（新故障、尝试开始、安装结果、门禁拒绝），
不写在每次回调路径上（规则 14：高频路径不得刷日志）。
"""

import threading

from logger import get_logger


class RecoveryState:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        """清空整个恢复事件（退出/停用/会话切换时调用）。"""
        with self.lock:
            self.reason = ""
            self.actions = set()
            self.verified = set()
            self.attempts = 0
            self.next_at = 0.0
            self.cooldown_until = 0.0
            self.cooldown_started = None
            self.state = "observing"
            self.noticed = False
            self.notice_at = 0.0
            self.recovery_noticed = False
            self.snapshot_taken = False
            self.last_started = None
            self.incident = 0

    def reset_batch(self):
        """只重置“这一批”尝试的计数与时间，保留故障本身（冷却结束后再试同一故障）。"""
        with self.lock:
            self.attempts = 0
            self.next_at = self.cooldown_until = 0.0
            self.cooldown_started = None
            self.last_started = None
            self.state = "observing"

    def fault(self, reason, actions):
        """记录一次异常证据。

        首个故障才开新事件（incident+1）并重置“已提示/已取快照/已恢复提示”标记；
        后续同事件内的证据只合并受影响动作，不覆盖原因、不重复计数。
        """
        with self.lock:
            if not self.reason:
                self.incident += 1
                self.noticed = self.snapshot_taken = self.recovery_noticed = False
                self.notice_at = 0.0
                self.reason = reason
                self.actions = set(actions)
                self.state = "observing"
                get_logger().debug("恢复策略: 新故障 reason=%s 动作数=%d", reason, len(actions))
            else:
                self.actions.update(actions)

    def permit(self, now, policy, manual=False):
        """门禁：现在是否允许开始一次尝试（只判定，不计数）。

        依次处理：手动绕过 → 次数上限（停止/冷却）→ 退避等待。
        返回 False 时同步写入 `state`，便于状态面板与日志解释“为什么没动”。
        """
        with self.lock:
            if manual and policy["hotkey_manual_bypass"]:
                return True
            limit = policy["hotkey_retry_limit"]
            if limit and self.attempts >= limit:
                if policy["hotkey_retry_exhausted"] == "stop":
                    self.state = "exhausted"
                    get_logger().debug("恢复策略: 已达尝试上限，停止自动恢复 attempts=%d", self.attempts)
                    return False
                if not self.cooldown_until:
                    # 冷却从“上次允许时刻”起算，避免等待期间被无谓延长。
                    self.cooldown_started = max(self.next_at, now)
                    self.cooldown_until = self.cooldown_started + policy["hotkey_retry_cooldown"]
                if now < self.cooldown_until:
                    self.state = "cooling-down"
                    get_logger().debug("恢复策略: 冷却中，剩余 %.1fs", self.cooldown_until - now)
                    return False
                # 冷却结束 ⇒ 开新一批，计数归零。
                self.attempts = 0
                self.cooldown_until = 0.0
                self.cooldown_started = None
                self.next_at = 0.0
            if self.state == "exhausted" and manual:
                return False
            if now < self.next_at:
                self.state = "waiting-backoff"
                get_logger().debug("恢复策略: 退避等待中，剩余 %.1fs", self.next_at - now)
                return False
            return True

    def try_start(self, now, policy, manual=False, reset_batch=False):
        """入队许可不是执行许可；检查与计数共用同一把锁。"""
        with self.lock:
            if not self.permit(now, policy, manual=manual):
                return False
            if reset_batch:
                self.reset_batch()
            self.started(now, policy)
            return True

    def started(self, now, policy):
        """记一次**实际开始**的尝试，并按固定/递增退避算出下次允许时刻。"""
        with self.lock:
            self.attempts += 1
            self.last_started = now
            interval = policy["hotkey_retry_interval"]
            if policy["hotkey_retry_mode"] == "backoff":
                # 指数退避，2**n 上限 16 防溢出；再夹到用户设置的最大间隔。
                interval = min(policy["hotkey_retry_max_interval"], interval * 2 ** min(16, self.attempts - 1))
            self.next_at = now + interval
            self.state = "attempting"
            get_logger().debug("恢复策略: 第 %d 次尝试开始，下次允许 %.1fs 后", self.attempts, interval)

    def reconfigure(self, policy):
        """设置变更后只重算“时间”类字段，不重置已发生的尝试计数与故障。"""
        with self.lock:
            if self.last_started is not None:
                interval = policy["hotkey_retry_interval"]
                if policy["hotkey_retry_mode"] == "backoff":
                    interval = min(policy["hotkey_retry_max_interval"], interval * 2 ** min(16, max(0, self.attempts - 1)))
                self.next_at = self.last_started + interval
            if self.cooldown_until and self.cooldown_started is not None:
                # 沿用原批次起点，改冷却时长不会把等待重新计时。
                self.cooldown_until = self.cooldown_started + policy["hotkey_retry_cooldown"]
            if not policy["hotkey_retry_limit"] or self.attempts < policy["hotkey_retry_limit"]:
                self.cooldown_until = 0.0
                self.cooldown_started = None
                if self.state in ("exhausted", "cooling-down"):
                    self.state = "awaiting-verification"

    def installed(self, success):
        """记录注册结果；成功也只是“待按键验证”，不等于热键已恢复。"""
        with self.lock:
            self.state = "awaiting-verification" if success else "registration-failed"
            get_logger().debug("恢复策略: 注册结果 %s", "成功(待验证)" if success else "失败")

    def callback(self, action):
        """无关动作不能解除故障；每个动作有自己的验证记录。

        返回该动作是否属于当前故障（供上层决定是否提示“恢复”）。
        注意：本方法在每次热键回调路径上被调用，故**不写任何日志**（规则 14）。
        """
        with self.lock:
            relevant = bool(self.reason) and action in self.actions
            self.verified.add(action)
            if relevant:
                self.actions.discard(action)
                if not self.actions:
                    # 故障涉及的动作全部真实回调到达 ⇒ 解除故障并清零本批计数。
                    self.state = "callback-verified"
                    self.reason = ""
                    self.attempts = 0
                    self.next_at = self.cooldown_until = 0.0
                    self.cooldown_started = None
                    self.last_started = None
            elif not self.reason and self.state == "awaiting-verification":
                self.state = "callback-verified"
            return relevant

    def notice_due(self, now, policy):
        """是否该发故障提示：首次模式只提示一次；重复模式按 `hotkey_notice_interval` 节流。"""
        with self.lock:
            mode = policy["hotkey_failure_notice"]
            due = bool(self.reason) and mode != "off" and (
                not self.noticed or mode == "repeat" and now - self.notice_at >= policy["hotkey_notice_interval"])
            if due:
                self.noticed = True
                self.notice_at = now
            return due

    def snapshot(self, now):
        """给诊断/状态面板的只读摘要：只含状态名、计数与剩余时间，不含用户输入内容。"""
        with self.lock:
            return dict(recovery_state=self.state, recovery_reason=self.reason,
                        incident=self.incident,
                        recovery_attempts=self.attempts,
                        retry_remaining=round(max(0.0, self.next_at - now), 1),
                        cooldown_remaining=round(max(0.0, self.cooldown_until - now), 1),
                        affected_actions=sorted(self.actions), verified_actions=sorted(self.verified))
