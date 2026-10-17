#!/usr/bin/env python
"""按 AGENTS「Validation」的口径跑测试：每个模块独立进程 + C4 判定 + 汇总表。

为什么需要它（见 AGENTS.md 的 Validation）：整套用例必须**分模块独立进程**跑 —— 同一进程里
模块之间会串状态，而且离屏平台在解释器收尾阶段会原生崩溃（C4：摘要有 OK、退出码却是
-1073741819/-1073740940）。把这条口径写进脚本，避免每次手工拼命令、也避免把 C4 误判成失败。

判定规则：
  有 "Ran N tests" + OK      → 通过（即使退出码非 0：C4 收尾崩溃）
  有 "Ran N tests" + FAILED  → 失败（列出 FAIL/ERROR 用例名）
  没有 "Ran N tests"         → 重试（默认 1 次）；仍无摘要 → NO-SUMMARY，脚本非零退出

用法：
    python run_tests.py                     # 全部模块
    python run_tests.py test_sticker misc   # 只跑名字里含这些子串的模块
    python run_tests.py --retries 2         # 无摘要时重试次数（默认 1）
    python run_tests.py --list              # 列出模块
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

MODULES = [
    "tests.test_config",
    "tests.test_capture",
    "tests.test_capture_pointer",
    "tests.test_sticker",
    "tests.test_sticker_mask",
    "tests.test_sticker_lifecycle",
    "tests.test_uia",
    "tests.test_dpi",
    "tests.test_ui",
    "tests.test_app",
    "tests.test_misc",
    "tests.test_elevation",
    "tests.test_supervisor",
    "tests.editor.test_canvas",
    "tests.editor.test_tools",
    "tests.editor.test_text",
    "tests.editor.test_erase",
    "tests.editor.test_zoom",
]

RAN_RE = re.compile(r"^Ran (\d+) tests?", re.MULTILINE)
FAIL_RE = re.compile(r"^(FAIL|ERROR): (\S+)", re.MULTILINE)
SKIP_RE = re.compile(r"skipped=(\d+)")

# 单模块预算与超时回收（AGENTS 规则 36）：无摘要/超时都不算通过，必须显示 TIMEOUT。
MODULE_TIMEOUT = 180
TREE_KILL_TIMEOUT = 10


def kill_tree(process):
    """超时后回收整棵进程树（Windows 用 taskkill /T /F），再做有限等待。"""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=TREE_KILL_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def run_module(module: str, retries: int):
    """跑一个模块，返回 (ran, verdict, failed, duration, note)。"""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    attempt = 0
    while True:
        attempt += 1
        started = time.monotonic()
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "unittest", module],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        timed_out = False
        try:
            output = proc.communicate(timeout=MODULE_TIMEOUT)[0] or ""
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(proc)
            try:
                output = proc.communicate(timeout=TREE_KILL_TIMEOUT)[0] or ""
            except subprocess.TimeoutExpired:
                output = ""
        duration = time.monotonic() - started
        if timed_out:
            return (0, "TIMEOUT", [], duration,
                    f"超过单模块预算 {MODULE_TIMEOUT}s ⇒ 已终止并回收进程树（不计通过）")
        match = RAN_RE.search(output)
        if match or attempt > retries:
            break
    if not match:
        hint = ("已重试仍无摘要（多半是 C4 收尾崩溃吃掉摘要，可再跑一次）；"
                "必要时分块跑：" + module + " -k <关键字>")
        return (0, "NO-SUMMARY", [], duration, hint)
    ran = int(match.group(1))
    failed = [f"{kind}: {name}" for kind, name in FAIL_RE.findall(output)]
    skipped = 0
    skip_match = SKIP_RE.search(output)
    if skip_match:
        skipped = int(skip_match.group(1))
    if failed:
        return (ran, "FAILED", failed, duration, "")
    note = ""
    if skipped:
        note = f"skipped={skipped}"
    if proc.returncode != 0:
        note = (note + " " if note else "") + "C4 收尾崩溃（摘要为 OK，判通过）"
    return (ran, "OK", [], duration, note)


def main() -> int:
    parser = argparse.ArgumentParser(description="按项目口径跑测试（分模块独立进程）")
    parser.add_argument("filters", nargs="*", help="模块名子串，留空表示全部")
    parser.add_argument("--retries", type=int, default=1, help="无摘要时的重试次数（默认 1）")
    parser.add_argument("--list", action="store_true", help="只列出模块")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    modules = [m for m in MODULES if not args.filters
               or any(f in m for f in args.filters)]
    if args.list:
        for m in modules:
            print(m)
        return 0
    if not modules:
        print("没有匹配的模块：" + ", ".join(args.filters))
        return 2

    print(f"跑 {len(modules)} 个模块（每个独立进程，QT_QPA_PLATFORM=offscreen）：")
    rows = []
    total_ran = 0
    failures = 0
    for module in modules:
        ran, verdict, failed, duration, note = run_module(module, args.retries)
        total_ran += ran
        if verdict != "OK":
            failures += 1
        rows.append((module, ran, verdict, failed, duration, note))
        line = f"  {module:<28} {ran:>4} 条  {verdict:<10} {duration:6.1f}s"
        if note:
            line += "  " + note
        print(line)
        for item in failed[:8]:
            print(f"        {item}")
        if len(failed) > 8:
            print(f"        …另有 {len(failed) - 8} 条")

    print("")
    print(f"合计 {total_ran} 条，模块 {len(modules)} 个，失败模块 {failures} 个")
    if failures:
        timeout_count = sum(1 for row in rows if row[2] == "TIMEOUT")
        no_summary = sum(1 for row in rows if row[2] == "NO-SUMMARY")
        detail = []
        if timeout_count:
            detail.append(f"TIMEOUT {timeout_count} 个")
        if no_summary:
            detail.append(f"无摘要 {no_summary} 个")
        suffix = ("（" + "、".join(detail) + "，不计通过）") if detail else "（真实失败，需修）"
        print("判定：FAILED" + suffix)
        return 1
    print("判定：全部通过（含 C4 收尾崩溃的模块按摘要判 OK）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())