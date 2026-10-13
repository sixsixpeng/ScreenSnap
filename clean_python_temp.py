#!/usr/bin/env python
"""清理 Python / unittest / pytest 运行时产生的临时文件。

默认根目录 = 脚本所在目录；可用位置参数指定其它根目录。

删除对象（整棵目录树遍历）：
  目录：__pycache__、.pytest_cache、.mypy_cache、.ruff_cache、.pytype、.pyre、
        .pyright、.hypothesis、.benchmarks、.nox、.tox、htmlcov、
        .ipynb_checkpoints、.eggs、pip-wheel-metadata、*.egg-info
  文件：*.pyc、*.pyo、.coverage / .coverage.<主机>.<pid>、coverage.xml、
        coverage.json、.dmypy.json
跳过目录（不进入、不删除，避免误删虚拟环境/IDE 配置里的内容）：
  .git .hg .svn .venv venv env .env __pypackages__ .idea .vscode .trae
  .codebuddy node_modules

说明：build/、dist/、*.spec 属于 PyInstaller 的**构建产物**（不是运行时临时文件），
      本脚本默认不清理，需要时请用项目里的 build.bat 或手动删除。

用法：
    python clean_python_temp.py                 # 预览后询问确认再删除
    python clean_python_temp.py -y              # 不询问，直接删除
    python clean_python_temp.py --dry-run       # 只预览，不删除
    python clean_python_temp.py D:\\proj -y     # 指定其它根目录
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# 不进入、不删除的目录名（虚拟环境、版本库、IDE 配置等），尾注为对应工具/软件
IGNORED_DIRS = {
    ".git",           # Git 版本库元数据
    ".hg",            # Mercurial 版本库元数据
    ".svn",           # Subversion 版本库元数据
    ".venv",          # Python 虚拟环境（venv / virtualenv 默认目录）
    "venv",           # Python 虚拟环境（venv 常见命名）
    "env",            # Python 虚拟环境（virtualenv 常见命名）
    ".env",           # Python 虚拟环境 / dotenv 约定的目录名
    ".idea",          # JetBrains PyCharm / IntelliJ IDEA 工程配置
    ".vscode",        # Visual Studio Code 工程配置
    ".trae",          # Trae IDE 工程配置
    ".codebuddy",     # CodeBuddy 插件工程配置
    "__pypackages__", # PDM / PEP 582 本地依赖目录（等价于虚拟环境）
    "node_modules",   # Node.js 依赖目录
}

# 匹配到即整目录删除的目录名（均为可自动重建的缓存 / 报告目录），尾注为生成来源
TEMP_DIR_NAMES = {
    "__pycache__",         # CPython 字节码缓存
    ".pytest_cache",       # pytest 用例缓存
    ".mypy_cache",         # mypy 增量缓存
    ".ruff_cache",         # Ruff 检查缓存
    ".pytype",             # pytype 类型检查缓存
    ".pyre",               # Pyre 类型检查缓存
    ".pyright",            # Pyright 类型检查缓存
    ".hypothesis",         # Hypothesis 属性测试数据库
    ".benchmarks",         # pytest-benchmark 基准结果
    ".nox",                # Nox 会话虚拟环境
    ".tox",                # Tox 会话虚拟环境
    "htmlcov",             # coverage.py 的 HTML 报告
    ".ipynb_checkpoints",  # Jupyter Notebook 自动检查点
    ".eggs",               # setuptools 旧式 egg 构建目录
    "pip-wheel-metadata",  # 旧版 pip 构建元数据
}
# 以这些后缀结尾的目录名也整个删除（如 ScreenSnap.egg-info）
TEMP_DIR_SUFFIXES = (".egg-info",)

# 匹配到即删除的文件后缀 / 文件名 / 文件名前缀，尾注为生成来源
TEMP_FILE_SUFFIXES = (".pyc", ".pyo")  # CPython 编译产物（散落在 __pycache__ 之外时）
TEMP_FILE_NAMES = {
    "coverage.xml",   # coverage.py 的 XML 报告
    "coverage.json",  # coverage.py 的 JSON 报告
    ".dmypy.json",    # mypy 守护进程（dmypy）状态缓存
}
TEMP_FILE_PREFIXES = (".coverage",)  # .coverage 与 .coverage.<主机>.<pid>


def is_temp_dir(name: str) -> bool:
    """判断目录名是否属于待删除的临时目录。"""
    if name in TEMP_DIR_NAMES:
        return True
    return name.endswith(TEMP_DIR_SUFFIXES)


def is_temp_file(name: str) -> bool:
    """判断文件名是否属于待删除的临时文件。"""
    lower = name.lower()
    if lower.endswith(TEMP_FILE_SUFFIXES):
        return True
    if lower in TEMP_FILE_NAMES:
        return True
    return any(lower == p or lower.startswith(p + ".") for p in TEMP_FILE_PREFIXES)


def collect(root: Path):
    """递归收集待删除的目录与文件，返回 (dirs, files)。

    用 os.walk(topdown=True) 并原地修改 dirnames 实现**剪枝**：
    命中待删目录 → 整体收集后不再深入；命中忽略目录 → 直接跳过。
    这样不会钻进 .venv 里把依赖包的 __pycache__ 一起删掉。
    """
    dirs: list[Path] = []
    files: list[Path] = []
    for current, dirnames, filenames in os.walk(root, topdown=True):
        cur = Path(current)
        kept: list[str] = []
        for name in dirnames:
            if name in IGNORED_DIRS:
                continue
            if is_temp_dir(name):
                dirs.append(cur / name)
            else:
                kept.append(name)
        dirnames[:] = kept  # 原地裁剪，阻止 os.walk 继续深入
        for name in filenames:
            if is_temp_file(name):
                files.append(cur / name)
    return dirs, files


def path_size(path: Path) -> int:
    """统计文件/目录占用的字节数（失败按 0 计，不影响清理流程）。"""
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total


def human(size: int) -> str:
    """把字节数格式化成易读字符串。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def delete_all(dirs: list[Path], files: list[Path]):
    """执行删除，返回 (成功数, 失败数)。单条失败不影响其它条目。"""
    removed = 0
    errors = 0
    for path in dirs:
        try:
            shutil.rmtree(path)
            removed += 1
        except OSError as exc:
            errors += 1
            print("    [失败] 目录 " + str(path) + " —— " + str(exc))
    for path in files:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            errors += 1
            print("    [失败] 文件 " + str(path) + " —— " + str(exc))
    return removed, errors


def show(path: Path, root: Path) -> str:
    """尽量以相对路径展示，失败则回退到绝对路径。"""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 Python / unittest / pytest 运行时临时文件")
    parser.add_argument("root", nargs="?", default=None,
                        help="要清理的根目录（默认脚本所在目录）")
    parser.add_argument("-y", "--yes", action="store_true", help="不询问，直接删除")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不执行删除")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root = Path(args.root).expanduser().resolve() if args.root else Path(__file__).resolve().parent
    if not root.is_dir():
        print("根目录不存在或不是目录：" + str(root))
        return 2

    dirs, files = collect(root)
    total_bytes = sum(path_size(p) for p in dirs) + sum(path_size(p) for p in files)

    print("扫描根目录：" + str(root))
    print("待删除目录：" + str(len(dirs)) + " 个")
    for path in dirs[:30]:
        print("    [目录] " + show(path, root))
    if len(dirs) > 30:
        print("    ……另有 " + str(len(dirs) - 30) + " 个目录")
    print("待删除文件：" + str(len(files)) + " 个")
    for path in files[:30]:
        print("    [文件] " + show(path, root))
    if len(files) > 30:
        print("    ……另有 " + str(len(files) - 30) + " 个文件")

    if not dirs and not files:
        print("没有发现需要清理的临时文件。")
        return 0

    print("预计可释放：" + human(total_bytes))

    if args.dry_run:
        print("（--dry-run：未做任何删除）")
        return 0

    if not args.yes:
        answer = input("确认删除以上内容？[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("已取消，未删除任何内容。")
            return 0

    removed, errors = delete_all(dirs, files)
    print("完成：删除 " + str(removed) + " 项，失败 " + str(errors) +
          " 项，释放 " + human(total_bytes))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
