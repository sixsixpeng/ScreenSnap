"""按日轮转的标准库日志。"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def configure_logging(settings):
    """重建轮转处理器，使设置页修改日志选项后立即生效。"""
    logger = logging.getLogger("screensnap")
    # 先关闭旧文件句柄，否则反复保存设置会重复写日志并占用文件。
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
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
    handler = TimedRotatingFileHandler(
        directory / "app.log", when=settings["log_when"], backupCount=14, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger