"""ScreenSnap 代码风险自查（2026-10-12 建立）。

用法：.venv\\Scripts\\python.exe tools\\risk_scan.py
不跑用例，只做两类静态检查（都是本项目真实发生过的坑）：
  ① 跨类属性误用：self.X 被读取，但本类（含项目内基类）既没有赋值也不是类属性/方法
     —— 真机案例：遮罩 MaskWindow 里写了 self.erasing（那是画布 AnnotationCanvas 的）
        ⇒ 每帧 AttributeError，日志被刷满。
  ② 绘制期改可见性：paintEvent / drawForeground / drawBackground / paint 里调用
     show / hide / setGeometry / setText / raise_ / setVisible
     —— 原生崩溃族：两次 0xC0000409 都源于绘制期改子控件或场景图元可见性。
允许少量误报（Mixin 的属性由组装类提供，已按被继承过的类跳过）；命中即返回 1。
"""

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FOLDERS = ("app", "core", "editor", "screenshot", "sticker", "ui", "config", "logger")
PAINT_FUNCS = ("paintEvent", "drawForeground", "drawBackground", "paint", "paintGL")
MUTATE_ATTRS = ("show", "hide", "setGeometry", "setText", "raise_", "setVisible", "setVisible")


def sources():
    files = [p for folder in FOLDERS for p in (ROOT / folder).rglob("*.py")]
    files.append(ROOT / "main.py")
    return [p for p in files if p.exists()]


def classes_of():
    info = {}
    for path in sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            entry = info.setdefault(node.name, {"attrs": set(), "bases": [], "loads": []})
            for base in node.bases:
                name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                if name:
                    entry["bases"].append(name)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    entry["attrs"].add(item.name)
                targets = item.targets if isinstance(item, ast.Assign) else (
                    [item.target] if isinstance(item, ast.AnnAssign) else [])
                for target in targets:
                    if isinstance(target, ast.Name):
                        entry["attrs"].add(target.id)
                    elif isinstance(target, ast.Attribute):
                        entry["attrs"].add(target.attr)
            for sub in ast.walk(node):
                if not (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)):
                    continue
                if sub.value.id != "self":
                    continue
                if isinstance(sub.ctx, ast.Load):
                    entry["loads"].append((path.as_posix(), sub.lineno, sub.attr))
                else:
                    entry["attrs"].add(sub.attr)
    return info


def pool(info, name, seen=None):
    seen = seen or set()
    if name in seen or name not in info:
        return set()
    seen.add(name)
    out = set(info[name]["attrs"])
    for base in info[name]["bases"]:
        out |= pool(info, base, seen)
    return out


def external_allowlist(info):
    """Qt 基类（项目类的父类里那些不属于本项目的名字）的成员白名单。

    没有它时会满屏误报：setScene / mapToScene / sceneRect / update 都来自 QGraphicsView 等基类。
    """
    allowed = set(dir(object))
    names = {b for entry in info.values() for b in entry["bases"] if b not in info}
    modules = []
    for module_name in ("PySide6.QtWidgets", "PySide6.QtCore", "PySide6.QtGui"):
        try:
            modules.append(__import__(module_name, fromlist=["x"]))
        except Exception:
            continue
    for name in names:
        hit = False
        for module in modules:
            cls = getattr(module, name, None)
            if isinstance(cls, type):
                try:
                    allowed |= set(dir(cls))
                except Exception:
                    pass
                hit = True
                break
        if hit:
            continue
        # 2026-10-12：还要覆盖 **stdlib** 基类，否则 `logger/log_setup.py` 继承
        # `TimedRotatingFileHandler` 会误报 acquire/_open/release ✓（实测 3 处误报 ✓）。
        for module_name in ("logging", "logging.handlers", "collections.abc",
                            "threading", "enum", "unittest"):
            try:
                module = __import__(module_name, fromlist=["x"])
            except Exception:
                continue
            cls = getattr(module, name, None)
            if isinstance(cls, type):
                try:
                    allowed |= set(dir(cls))
                except Exception:
                    pass
                break
    return allowed

def scan_attributes(info):
    inherited = {b for e in info.values() for b in e["bases"]}
    allowed = external_allowlist(info)
    found = []
    for name, entry in sorted(info.items()):
        if name in inherited or name.endswith("Mixin"):
            continue
        known = pool(info, name)
        for path, lineno, attr in entry["loads"]:
            if attr.startswith("__") or attr in known or attr in allowed:
                continue
            found.append((path + ":" + str(lineno), name + "." + attr))
    return found


def scan_paint():
    """扫"绘制期改可见性"：从 paint/draw 函数出发，沿**同一模块内的调用图**传递。

    2026-10-12 增强：原先只扫函数名像 paint/draw 的函数 ✗ ⇒ 扫不到被 drawForeground 间接调用的
    `_paint_erased_composite` / `_render_annotation_layer_now` 这类函数 ✓（真实风险恰在那里面 ✓）。
    """
    found = []
    for path in sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        funcs = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.setdefault(node.name, node)
        calls = {}
        for name, node in funcs.items():
            called = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    target = sub.func
                    if isinstance(target, ast.Attribute):
                        called.add(target.attr)
                    elif isinstance(target, ast.Name):
                        called.add(target.id)
            calls[name] = called & set(funcs)
        pending = [n for n in funcs if n in PAINT_FUNCS]
        reached = set()
        while pending:
            current = pending.pop()
            if current in reached:
                continue
            reached.add(current)
            pending.extend(calls.get(current, ()))
        for name in sorted(reached):
            node = funcs[name]
            # 按 **AST 调用节点**判定（不是扫文本 ✗）：文档字符串/注释里出现 `.hide()` 之类不再误报 ✓。
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call) or not isinstance(sub.func, ast.Attribute):
                    continue
                if sub.func.attr not in MUTATE_ATTRS:
                    continue
                where = path.as_posix() + ":" + str(sub.lineno)
                found.append((where, name + "  ." + sub.func.attr + "()"))

    return found

def scan_paint_pointer():
    """绘制期**移动指针**或**同步重绘**（`QCursor.setPos` / `repaint()`）—— 见 AGENTS 经验 D37 ✓。

    指针只允许在"用户按了键盘微调键"时被移动 ✓；绘制期动指针属于历史 bug 族（画布吸附鼠标 ✗）✓。
    """
    found = []
    for path in sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        funcs = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        calls = {}
        for name, node in funcs.items():
            called = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    target = sub.func
                    if isinstance(target, ast.Attribute):
                        called.add(target.attr)
                    elif isinstance(target, ast.Name):
                        called.add(target.id)
            calls[name] = called & set(funcs)
        pending = [n for n in funcs if n in PAINT_FUNCS]
        reached = set()
        while pending:
            current = pending.pop()
            if current in reached:
                continue
            reached.add(current)
            pending.extend(calls.get(current, ()))
        for name in sorted(reached):
            for sub in ast.walk(funcs[name]):
                if not isinstance(sub, ast.Call):
                    continue
                target = sub.func
                if isinstance(target, ast.Attribute) and target.attr == "setPos":
                    base = target.value
                    if isinstance(base, ast.Name) and base.id == "QCursor":
                        found.append((path.as_posix() + ":" + str(sub.lineno),
                                      name + "  QCursor.setPos()"))
                elif isinstance(target, ast.Attribute) and target.attr == "repaint":
                    found.append((path.as_posix() + ":" + str(sub.lineno),
                                  name + "  repaint()"))
                elif isinstance(target, ast.Name) and target.id == "repaint":
                    found.append((path.as_posix() + ":" + str(sub.lineno), name + "  repaint()"))
    return found


def main():
    info = classes_of()
    problems = 0
    for title, items in (("① 跨类属性误用", scan_attributes(info)),
                         ("② 绘制期改可见性", scan_paint()),
                         ("③ 绘制期动指针/同步重绘", scan_paint_pointer())):
        print("")
        print(title + "：" + str(len(items)) + " 处")
        for where, detail in items[:20]:
            print("    " + where.ljust(52) + " " + detail)
        problems += len(items)
    print("")
    print("合计待确认：" + str(problems) + " 处（允许少量误报，逐条判定即可）")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())