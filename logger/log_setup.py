"""按日轮转的标准库日志。"""

import logging
import sys
from datetime import datetime
import faulthandler
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from logger.log_context import ContextFilter


class MonthlyDirectoryTimedRotatingFileHandler(TimedRotatingFileHandler):
    """按月切换日志目录，同时保留配置的日/小时文件轮转。"""

    def __init__(self, directory, filename, **kwargs):
        self.directory = Path(directory)
        self.filename = filename
        self.month = datetime.now().strftime("%Y-%m")
        target = self.directory / self.month / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(target, **kwargs)

    def emit(self, record):
        month = datetime.now().strftime("%Y-%m")
        if month != self.month:
            self.acquire()
            try:
                if self.stream:
                    self.stream.flush()
                    self.stream.close()
                    self.stream = None
                self.month = month
                target = self.directory / month / self.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                self.baseFilename = str(target.resolve())
                self.stream = self._open()
            finally:
                self.release()
        super().emit(record)


def configure_logging(settings):
    """重建轮转处理器，使设置页修改日志选项后立即生效。"""
    logger = logging.getLogger("screensnap")
    # 先关闭旧文件句柄，否则反复保存设置会重复写日志并占用文件。
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    # 现场信息过滤器只保留一个，反复保存设置不会重复叠加。
    for item in logger.filters[:]:
        logger.removeFilter(item)
    logger.disabled = not settings["logging_enabled"]
    if logger.disabled:
        return logger
    level = settings["log_level"].upper()
    # 标准库未内建 TRACE，这里为更细粒度日志分配低于 DEBUG 的等级。
    logging.addLevelName(5, "TRACE")
    logger.setLevel(5 if level == "TRACE" else getattr(logging, level, logging.INFO))
    requested = Path(settings.get("log_dir") or "").expanduser()
    if settings.get("log_dir") and requested.is_dir():
        directory = requested
    else:
        startup = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
        directory = Path(startup).resolve().parent / "logs"
        directory.mkdir(parents=True, exist_ok=True)
    if settings.get("log_monthly_folder", True):
        handler = MonthlyDirectoryTimedRotatingFileHandler(
            directory, "app.log", when=settings["log_when"], backupCount=14,
            encoding="utf-8")
    else:
        handler = TimedRotatingFileHandler(
            directory / "app.log", when=settings["log_when"], backupCount=14,
            encoding="utf-8")
    logger.addFilter(ContextFilter())
    # 位置信息用 模块:行号 函数名，方括号内是鼠标、前台窗口与当前操作对象。
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(module)s:%(lineno)d %(funcName)s() [%(context)s] %(message)s"))
    logger.addHandler(handler)
    # 控制台同步输出，方便直接观察；打包为无控制台程序时 sys.stdout 为 None。
    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(module)s:%(lineno)d [%(context)s] %(message)s"))
        logger.addHandler(console)
    # 必须在 return 之前调用：曾经写在 return 之后，等于死代码，crash.log 从未生成（2026-10-10 实测）。
    enable_crash_dumps(directory)
    return logger


# 原生崩溃（访问违规/堆损坏）不会走 sys.excepthook，日志里什么都留不下。faulthandler 会在
# 致命信号时把所有线程的 Python 栈写进 crash.log —— 这是「日志无输出却崩溃」的唯一现场证据。
_CRASH_STREAM = None


def enable_crash_dumps(directory):
    """把致命错误（访问违规等）的 Python 栈写入日志目录下的 crash.log。"""
    global _CRASH_STREAM
    if _CRASH_STREAM is not None:
        return
    try:
        path = (directory / "crash.log") if hasattr(directory, "__truediv__") \
            else (str(directory) + "/crash.log")
        stream = open(path, "a", encoding="utf-8")
        faulthandler.enable(file=stream, all_threads=True)
        _CRASH_STREAM = stream
        # 确认日志：这一行必须出现在 app.log 里，否则说明崩溃转储没启用（crash.log 也不会生成）。
        logging.getLogger("screensnap").info("崩溃转储已启用: %s", path)
    except (OSError, ValueError, RuntimeError):
        pass
