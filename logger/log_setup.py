"""按日轮转的标准库日志。"""

import logging
import os
import sys
from datetime import datetime
import faulthandler
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from logger.log_context import ContextFilter

# 全项目共用的固定日志器名。写成常量是为了避免各模块手抄魔法字符串：拼错（如
# "screansnap"）不会报错，而是静默创建另一个日志器，日志既不落盘也不进控制台。
# 获取入口统一走 `get_logger()`（定义在本模块而不是 `logger/__init__.py`，避免
# logger 包 → log_setup → logger 包的循环导入）。
LOGGER_NAME = "screensnap"


def get_logger(name=None):
    """取日志器：默认取项目主日志器；给 name 则取其子日志器（便于按模块单独过滤）。"""
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")


class ColoredFormatter(logging.Formatter):
    """控制台专用的彩色格式器：按等级给整行加 ANSI 颜色。

    只给 StreamHandler 用；文件处理器**一定不要**用 —— 否则日志文件里会混入转义码，
    既污染文本，也让 grep/解析变难。是否上色由 `_console_supports_color()` 决定。
    """

    COLORS = {
        5: "\033[90m",                    # TRACE（自定义等级）：暗灰
        logging.DEBUG: "\033[36m",        # 青
        logging.INFO: "\033[32m",         # 绿
        logging.WARNING: "\033[33m",      # 黄
        logging.ERROR: "\033[31m",        # 红
        logging.CRITICAL: "\033[41;97m",  # 红底白字
    }
    RESET = "\033[0m"

    def format(self, record):
        text = super().format(record)
        color = self.COLORS.get(record.levelno)
        return f"{color}{text}{self.RESET}" if color else text


def _console_supports_color(stream):
    """控制台是否该上色：默认只在真实终端且能启用 VT 时开。

    - `NO_COLOR`（业界约定）一旦存在即关闭；
    - `SCREENSNAP_LOG_COLOR=1/0` 可强制开/关（排查配色，或输出被重定向时用）。
    """
    if os.environ.get("NO_COLOR"):
        return False
    flag = os.environ.get("SCREENSNAP_LOG_COLOR", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    try:
        if not stream.isatty():
            return False   # 重定向到文件/管道时不加颜色，避免转义码污染输出
    except (AttributeError, ValueError):
        return False
    return _enable_windows_ansi()


def _enable_windows_ansi():
    """Windows 10+ 必须显式打开 ENABLE_VIRTUAL_TERMINAL_PROCESSING，否则颜色显示成乱码。"""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)   # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError, ValueError):
        return False


# 记录没写过 context 属性时（例如绕过 ContextFilter 的诊断记录）用它兜底，避免格式化报错。
_FIELD_DEFAULTS = {"context": "-"}


def log_directory(settings):
    requested = Path(settings.get("log_dir") or "").expanduser()
    if settings.get("log_dir") and requested.is_dir():
        return requested
    startup = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    return Path(startup).resolve().parent / "logs"


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
    logger = get_logger()
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
    directory = log_directory(settings)
    directory.mkdir(parents=True, exist_ok=True)
    if settings.get("log_monthly_folder", True):
        handler = MonthlyDirectoryTimedRotatingFileHandler(
            directory, "app.log", when=settings["log_when"], backupCount=settings.get("log_backup_count", 14),
            encoding="utf-8")
    else:
        handler = TimedRotatingFileHandler(
            directory / "app.log", when=settings["log_when"], backupCount=settings.get("log_backup_count", 14),
            encoding="utf-8")
    logger.addFilter(ContextFilter())
    # 位置信息用 文件:行号 函数名，方括号内是鼠标、前台窗口与当前操作对象；
    # 文件日志额外带 pid 与线程名 —— 热键回调跑在后台线程，排查时要能区分来源。
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(funcName)s() "
        "[%(context)s] pid=%(process)d %(threadName)s %(message)s",
        defaults=_FIELD_DEFAULTS))
    logger.addHandler(handler)
    # 控制台同步输出，方便直接观察；打包为无控制台程序时 sys.stdout 为 None。
    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console_format = ("%(asctime)s %(levelname)s %(filename)s:%(lineno)d %(funcName)s() "
                          "[%(context)s] %(message)s")
        # 颜色只给控制台；重定向/管道/未启用 VT 时自动退回无色格式。
        if _console_supports_color(sys.stdout):
            console.setFormatter(ColoredFormatter(console_format, defaults=_FIELD_DEFAULTS))
        else:
            console.setFormatter(logging.Formatter(console_format, defaults=_FIELD_DEFAULTS))
        logger.addHandler(console)
    # 必须在 return 之前调用：曾经写在 return 之后，等于死代码，crash.log 从未生成（2026-10-10 实测）。
    enable_crash_dumps(directory)
    return logger


# 原生崩溃（访问违规/堆损坏）不会走 sys.excepthook，日志里什么都留不下。faulthandler 会在
# 致命信号时把所有线程的 Python 栈写进 crash.log —— 这是「日志无输出却崩溃」的唯一现场证据。
_CRASH_STREAM = None
_CRASH_PATH = None


def close_crash_dumps():
    """关闭崩溃转储流（主要供测试收尾调用，避免临时目录句柄删不掉）。

    产品运行期不需要调用：crash.log 按设计在整个进程内保持打开，才能留住崩溃现场。
    """
    global _CRASH_STREAM, _CRASH_PATH
    if _CRASH_STREAM is None:
        return
    try:
        faulthandler.disable()
    except Exception:  # noqa: BLE001
        pass
    try:
        _CRASH_STREAM.close()
    except Exception:  # noqa: BLE001
        pass
    _CRASH_STREAM = None
    _CRASH_PATH = None


def enable_crash_dumps(directory):
    """把致命错误（访问违规等）的 Python 栈写入日志目录下的 crash.log。

    日志目录可能在运行中被改（设置页的 `log_dir`，或测试里的临时目录），所以这里比较
    目标路径：目录没变就复用已打开的流；变了就先关旧流、再重开到新目录。
    2026-10-11 审计发现：原先只要启用过一次就直接 return，导致改了日志目录后 crash.log
    仍写在旧目录（真机可见），也是两条用例永远删不掉临时目录（WinError 32）的根因。
    """
    global _CRASH_STREAM, _CRASH_PATH
    path = (directory / "crash.log") if hasattr(directory, "__truediv__") \
        else (str(directory) + "/crash.log")
    path = str(path)
    if _CRASH_STREAM is not None and _CRASH_PATH == path:
        return
    if _CRASH_STREAM is not None:
        # 先摘掉 faulthandler 再关流，避免它继续往已关闭的文件里写。
        try:
            faulthandler.disable()
        except Exception:  # noqa: BLE001
            pass
        try:
            _CRASH_STREAM.close()
        except Exception:  # noqa: BLE001
            pass
        _CRASH_STREAM = None
        _CRASH_PATH = None
    try:
        stream = open(path, "a", encoding="utf-8")
        faulthandler.enable(file=stream, all_threads=True)
        _CRASH_STREAM = stream
        _CRASH_PATH = path
        # 确认日志：这一行必须出现在 app.log 里，否则说明崩溃转储没启用（crash.log 也不会生成）。
        get_logger().info("崩溃转储已启用: %s", path)
    except (OSError, ValueError, RuntimeError):
        pass
