"""通知音效播放（A+B+C，2026-10-11）。

三种来源，全部只用标准库，不引入 QtMultimedia：
· default     —— QApplication.beep()，即 Windows「默认提示音」（用户可在系统声音方案里换）；
· 四种系统音  —— winsound.MessageBeep(MB_ICON*)，对应系统声音里的 星号/感叹号/问号/关键停止，
                 同样可在 Windows 声音方案里替换成任意 wav；
· builtin1-4 —— 随包分发的 4 个短音（ui/assets/sounds/*.wav），**与 Windows 声音方案无关** ——
                 2026-10-11 用户反馈"星号与感叹号听起来一样、问号没声"（Windows 默认把前两者指向同一个 wav，
                 问号常常未分配）⇒ 内置这 4 个保证四个选项**互不相同且一定有声音**；
· custom      —— winsound.PlaySound(<用户选择的 wav>, SND_FILENAME | SND_ASYNC)。

任何失败（非 Windows、文件不存在、播放异常）都回落到 beep，绝不抛异常影响通知本身。
"""

import logging
import os
from pathlib import Path

# 内置提示音：键 → 随包 wav。加进 SOUND_KINDS 后 config.validate() 自动接受它们。
BUILTIN_FILES = {"builtin1": "chime1.wav", "builtin2": "chime2.wav",
                 "builtin3": "chime3.wav", "builtin4": "chime4.wav",
                 "builtin5": "chime5.wav", "builtin6": "chime6.wav",
                 "builtin7": "chime7.wav", "builtin8": "chime8.wav",
                 "builtin9": "chime9.wav", "builtin10": "chime10.wav",
                 "builtin11": "chime11.wav", "builtin12": "chime12.wav",
                 "builtin13": "chime13.wav", "builtin14": "chime14.wav",
                 "builtin15": "chime15.wav", "builtin16": "chime16.wav",
                 "builtin17": "chime17.wav", "builtin18": "chime18.wav",
                 "builtin19": "chime19.wav", "builtin20": "chime20.wav",
                 "builtin21": "chime21.wav", "builtin22": "chime22.wav",
                 "builtin23": "chime23.wav", "builtin24": "chime24.wav",
                 "builtin25": "chime25.wav", "builtin26": "chime26.wav",
                 "builtin27": "chime27.wav", "builtin28": "chime28.wav",
                 "builtin29": "chime29.wav", "builtin30": "chime30.wav"}
LEGACY_KINDS = {"exclamation": "asterisk", "question": "default"}

# 合法取值：default / 四种系统音 / 4 个内置音 / custom（自定义 wav）。空串由调用方解释为「跟随全局」。
SOUND_KINDS = ("default", "asterisk", "exclamation", "question", "hand", "custom",
               *BUILTIN_FILES)


def builtin_dir():
    """内置音效目录：源码与 PyInstaller 均使用 ui/assets/sounds。"""
    import sys

    bundled = getattr(sys, "_MEIPASS", None)
    root = Path(bundled) if bundled else Path(__file__).resolve().parent.parent
    return root / "ui" / "assets" / "sounds"


def builtin_path(kind):
    """内置音对应的 wav 路径；不是内置音或文件缺失时返回 None。"""
    name = BUILTIN_FILES.get(str(kind or "").strip().lower())
    if not name:
        return None
    path = builtin_dir() / name
    return path if path.is_file() else None

# winsound.MessageBeep 的取值。
_MB_ICON = {
    "asterisk": 0x00000040,      # MB_ICONASTERISK
    "exclamation": 0x00000030,   # MB_ICONEXCLAMATION
    "question": 0x00000020,      # MB_ICONQUESTION
    "hand": 0x00000010,          # MB_ICONHAND（关键停止）
}


def resolve_kind(kind, path=""):
    """把配置值规整成可播放的形式：未知取值回落 default；custom 但文件不可用也回落 default。"""
    value = str(kind or "default").strip().lower()
    value = LEGACY_KINDS.get(value, value)
    if value not in SOUND_KINDS:
        return "default", ""
    if value == "custom":
        target = str(path or "")
        if not target or not os.path.isfile(target):
            return "default", ""
        return "custom", target
    return value, ""


def play_sound(kind="default", path=""):
    """播放一次提示音；返回实际播放的类型（便于用例断言与排查）。"""
    logger = logging.getLogger("screensnap")
    resolved, target = resolve_kind(kind, path)
    if os.name == "nt":
        try:
            import winsound

            if resolved == "custom":
                winsound.PlaySound(target, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return resolved
            if resolved in BUILTIN_FILES:
                builtin = builtin_path(resolved)
                if builtin is None:
                    resolved = "default"   # 随包文件缺失（异常安装）⇒ 回落默认提示音
                else:
                    winsound.PlaySound(str(builtin), winsound.SND_FILENAME | winsound.SND_ASYNC)
                    return resolved
            if resolved in _MB_ICON:
                winsound.MessageBeep(_MB_ICON[resolved])
                return resolved
        except Exception as error:  # noqa: BLE001 音效失败绝不能影响通知
            logger.warning("播放提示音失败，回落系统默认提示音: %s", error)
    try:
        from PySide6.QtWidgets import QApplication

        QApplication.beep()
    except Exception as error:  # noqa: BLE001
        logger.warning("系统提示音也不可用: %s", error)
    return "default"