"""诊断导出只接收显式元数据；原始日志需UI单独确认。

安全约束（顺序即判定顺序）：后缀必须与是否附带日志一致 → 目标不得落在运行日志目录、
源码 logs 目录或用户数据目录内 → 报告本身必须是合法 JSON 结构化元数据。
写入用同目录临时文件 + 原子替换，失败时旧文件保留；临时文件在 finally 中清理。
"""

import json
from pathlib import Path
import re
import tempfile
import zipfile

from logger import get_logger


def export_report(target, report, log_root, source_root, config_path, include_logs=False):
    target = Path(target).resolve()
    expected_suffix = ".zip" if include_logs else ".json"
    if target.suffix.lower() != expected_suffix:
        get_logger().warning("诊断报告导出被拒: 后缀不符 expected=%s", expected_suffix)
        raise ValueError(f"诊断报告必须保存为{expected_suffix}文件，不能覆盖程序或其他格式文件")
    log_root = Path(log_root).resolve()
    source_logs = (Path(source_root) / "logs").resolve()
    private_root = Path(config_path).resolve().parent
    for protected in (log_root, source_logs, private_root):
        if target == protected or protected in target.parents:
            get_logger().warning("诊断报告导出被拒: 目标位于受保护目录")
            raise ValueError("不能把诊断报告写入运行日志或用户数据目录")
    payload = json.loads(report)  # 必须是合法的结构化元数据，不接受对象repr或任意文件拼接。
    temporary = None
    omitted = 0
    truncated = 0
    try:
        with tempfile.NamedTemporaryFile(prefix="screensnap-report-", suffix=".tmp", dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
        if include_logs:
            sources = []
            seen = set()
            for root in dict.fromkeys((log_root, source_logs)):
                if not root.is_dir():
                    continue
                try:
                    directories = [root] + sorted([p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}", p.name)],
                                                  key=lambda p: p.name, reverse=True)
                except OSError:
                    omitted += 1
                    continue
                for folder in directories[:61]:
                    try:
                        for path in folder.iterdir():
                            resolved = path.resolve()
                            if root not in resolved.parents or not resolved.is_file() or resolved in seen:
                                continue
                            if path.name in ("app.log", "crash.log", "supervisor.log") or path.name.startswith("app.log."):
                                priority = 0 if path.name == "app.log" else 1 if path.name in ("crash.log", "supervisor.log") else 2
                                sources.append((priority, -path.stat().st_mtime, root, path))
                                seen.add(resolved)
                    except OSError:
                        omitted += 1
            # 排序键：优先级(0=当前 app.log，1=crash/supervisor，2=历史轮转) → 修改时间新者在前。
            sources.sort(key=lambda entry: (entry[0], entry[1]))
            # 最多附带 20 份，其余只计数、不静默漏掉（调用方会把数量显示给用户）。
            truncated = max(0, len(sources) - 20)
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("diagnostic.json", json.dumps(payload, ensure_ascii=False, indent=2))
                for index, (_, _, root, path) in enumerate(sources[:20]):
                    try:
                        # 单文件最多尾部1MiB，最多20份；原始内容不承诺脱敏。
                        with path.open("rb") as stream:
                            stream.seek(0, 2)
                            stream.seek(max(0, stream.tell() - 1024 * 1024))
                            data = stream.read(1024 * 1024)
                        archive.writestr(f"raw-logs/{index:02d}/{path.relative_to(root).as_posix()}", data)
                    except OSError:
                        omitted += 1
                archive.writestr("NOTICE.txt", "Raw logs may contain window titles, paths and command lines.\n"
                                 f"At most 20 files, last 1 MiB each; unreadable sources: {omitted}; additional files omitted: {truncated}.\n")
        else:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        # 成功导出留痕：连同“未读到/超限未附带”的数量一起记录，便于排查报告为何不完整。
        get_logger().info("诊断报告已导出: 附带原始日志=%s 未读=%d 超限未附带=%d",
                       include_logs, omitted, truncated)
        return {"written": True, "unreadable_logs": omitted, "truncated_logs": truncated}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
