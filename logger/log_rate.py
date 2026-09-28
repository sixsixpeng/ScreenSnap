"""高频日志的节流：同一状态在指定时间内只记录一次。"""

import logging
import time

_stamps = {}


def throttled(channel, key, interval):
    """状态变化立即记录；状态不变时至少间隔 interval 秒才再记录。"""
    stamp = time.monotonic()
    last = _stamps.get(channel)
    if last is not None and last[0] == key and stamp - last[1] < interval:
        return False
    _stamps[channel] = (key, stamp)
    return True


def log_every(logger, level, channel, key, interval, message, *args):
    """按通道节流后记录一条日志，避免拖动和定时器轮询刷屏。"""
    if throttled(channel, key, interval):
        logger.log(level, message, *args)
