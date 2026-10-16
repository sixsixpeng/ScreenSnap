"""日志包：统一获取入口 + 对外暴露配置。"""

from logger.log_setup import LOGGER_NAME, configure_logging, get_logger

__all__ = ["configure_logging", "LOGGER_NAME", "get_logger"]