"""日志分级、轮转和保存位置配置。"""

from ui.widgets.file_path_edit import FilePathEdit
from ui.widgets.tooltip import SettingsPage


class LogPage(SettingsPage):
    """控制标准库日志的开关、等级、轮转周期和保存位置。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("日志记录")
        self.check("logging_enabled", "启用日志", "关闭后停止写入运行日志；\n"
                                                  "默认写入 logs/YYYY-MM/app.log；排查问题时请先确认这一项是开启的")
        self.choice("log_level", "日志等级",
                    [(name, name) for name in ("ERROR", "WARNING", "INFO", "DEBUG", "TRACE")],
                    "INFO（默认）：启动、截图、保存、贴图创建、窗口识别结果、吸附与跟随的启停；\n"
                    "DEBUG：额外记录坐标换算、窗口句柄/标题/类名/矩形、吸附候选与跟随位移，\n"
                    "排查“识别不到窗口/控件”或“吸附位置不对”时临时打开；\n"
                    "DEBUG：额外记录每次坐标换算、窗口命中的句柄/标题/矩形、候选计算与跟随位移；\n"
                    "TRACE 记录最细致的信息；只想看异常时选 WARNING 或 ERROR")
        self.choice("log_when", "分割周期", [("每天", "midnight"), ("每小时", "H")],
                    "最多保留 14 份日志；按小时分割适合短期密集排查")
        self.check("log_monthly_folder", "按月份创建日志文件夹",
               "开启后按 YYYY-MM 创建子目录；跨月后新日志会自动写入新的月份目录。\n"
               "关闭后日志直接写入所选目录。")
        directory = FilePathEdit(config.data["log_dir"],
                     lambda value: self.update_value("log_dir", value))
        directory.setToolTip("仅使用已存在的目录；留空或目录不存在时保存到启动文件所在目录的 logs 文件夹")
        self.controls["log_dir"] = directory
        self.form.addRow("日志保存目录", directory)