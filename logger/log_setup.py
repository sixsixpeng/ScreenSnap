"""按日轮转的标准库日志。"""

import logging
import sys
from datetime import datetime
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
    return logger