# ScreenSnap Agent Guide

## Project Scope

- This file applies to the ScreenSnap project rooted at this directory.
- The application is Windows-first and uses Python, PySide6, Pillow, and unittest.
- Keep changes within the owning modules and follow the existing Qt Widgets and settings patterns.

## Change Workflow

1. Trace the code that directly decides the behavior, then inspect its callers and nearby tests.
2. Keep preview-only UI state non-destructive. Do not mutate source screenshots or undo history to achieve a visual preview.
3. For configuration changes, update defaults and validation, settings UI, live refresh paths, and tests as applicable.
4. For every feature or behavior change, assess whether it is a persistent user preference: consider its scope, frequency, and owning domain; if configurable, name the settings page/group and justify the placement, and update defaults, validation, reset/import/export, runtime refresh, tests, and README. Keep one-shot commands and internal mechanics out of settings unless users need a durable choice.
5. For every completed feature or behavior-adjustment round, update the matching README instructions and append a dated entry under `README.md`'s `调整记录` section. Each entry should capture the user-visible change, important implementation boundary, and focused verification performed. If a date is unknown during a historical backfill, label it as undated rather than guessing.
6. Treat configuration as a complete lifecycle: cover first-run defaults, legacy/missing-key initialization, validation and repair, settings UI, persistence, reset/import/export, runtime refresh, and tests. Document the user-facing default and every supported value in README.
7. Consider both standalone `EditorWindow` and capture-local `InlineEditor` whenever changing shared editor controls or workflows. Keep capture-only commands in the screenshot mask or standalone editor as appropriate; add explicit tests for both contexts when button visibility or command routing differs.
8. Keep settings-page previews and editor-toolbar previews synchronized from the same configuration source. Preview changes must remain non-destructive and must not alter source images or undo history.
9. Tooltips use plain text with a concise explanation; preserve the platform's default tooltip appearance and avoid HTML/CSS decoration.
10. Preserve distinct screenshot gestures: left-button confirmation/double-click follows `capture_after_selection`; right-button double-click and the configured quick-save shortcut always save directly. Cover each path independently in tests.
11. Entering an editor from a capture must not write files by itself: opening the editor only shows it, and the image is written when the user explicitly saves or asks for a sticker. Keep the save-failure path and never emit more than one save notification for a single capture.
12. Prefer shared editor primitives with explicit context adapters over duplicating the annotation canvas or merging the two window lifecycles. `InlineEditor` and standalone `EditorWindow` have different ownership, sizing, capture, and close semantics; consider both before changing shared state.
13. Every new feature or behavior change must add logs at meaningful control and failure boundaries. Use `DEBUG`/`TRACE` for diagnostic details, `INFO` for user-visible lifecycle milestones, `WARNING` for recoverable unexpected conditions, and `ERROR` for failed operations that need attention. Include actionable context without logging image pixels, clipboard contents, or sensitive user data.
14. Keep logs quiet on high-frequency paths: never log each timer tick, pointer move, paint, polling iteration, or unchanged state. Log transitions, throttled summaries, or exceptional exits instead; test repeated interactions to ensure they do not flood the log.
15. Update this guide when the project structure, required workflow, test commands, or documentation policy changes.
16. `Application` is assembled from the `app/*_flow.py` mixins. Tests stub behaviour with `patch("main.X")`, so moving a method between modules must move its patch target too; when a name is both constructed and type-checked in different modules (e.g. `MaskWindow`), keep it patchable in both.
17. Keep UIA queries on the thread that calls them. Never move them to a worker thread: `set_click_through` calls `SetWindowLong` on the Qt-owned screenshot mask, and doing that off-thread deadlocks the UI — the mask covers the whole screen, so the freeze looks system-wide. Keep the `SLOW_SECONDS` breaker, the read budget and `deepest_only` hover path instead.
18. Keep the screenshot mask responsive: cache the composed mask background as a pixmap, and never run the hover query inside `mouseMoveEvent` (defer it to the next event-loop turn), otherwise the crosshair lags behind the pointer.
19. A test that genuinely cannot run without a real Windows desktop (window handles or focus state, a screen wider than the editor) is left as an **empty placeholder**: a one-line comment naming what needs manual verification, `@unittest.skip` with a short reason, and an empty body. Do not keep a long body, a docstring essay, or assertions against mocked substitutes.
20. Whenever a user-visible feature or behaviour is **added, changed or removed**, record it in `RELEASE_NOTES.md` in the same round, under the **matching feature section**: numbering is per section (`3.5` = section 3, entry 5), so append the section's next number — never renumber existing entries — and add a new section when the change does not fit an existing one. Record removals the same way. `RELEASE_NOTES.md` is a packaging/release aid and must **not** be committed — never `git add` it, keep it untracked.

21. **Never commit on your own initiative.** Finish the work, verify it, then tell the user it is ready and suggest the commit message(s); run `git add`/`git commit` only after they explicitly ask. Do not bring up committing every round — mention it only when the user asks, when work is clearly finished and waiting, or when an uncommitted change would block or mislead the next step.

22. Coordinate spaces are a known source of drift bugs (UIA highlight offset, screenshots or stickers landing on the neighbouring monitor). Three spaces exist and must never be mixed: **Qt-native global** (`QCursor.pos`, `mapToGlobal`, `screenAt`, `setGeometry`, `move`), the **mask's compacted logical space** (`DisplayMapper.logical`, internal, mask-local math only), and **physical pixels** (Win32 `GetWindowRect`, DWM, UIA, mss). Convert Qt-global values with `native_global_to_physical_global` / `physical_global_to_native_global` and use `physical_local_to_logical_local` together with its inverse only inside the mask. Never feed a Qt-global value into the `logical_*` conversions, and never feed a physical rect into a Qt API. Any coordinate or size change must cover every affected path, not just the one being fixed: capture, capture-to-sticker (Space quick sticker, inline editor, right-click direct save), sticker creation from clipboard/file/text/colour, sticker restore (with position+size, position only, or neither — always re-derive the size from `scale_factor` and the **current** screen dpr, never trust a stored pixel size), sticker loading and cross-screen dragging, both editors, notification and recycle-bin thumbnails, and the 2/3/4-screen arrangements (row, column, grid, diagonal stagger, physical gaps, negative origins) across 1080/2K/4K at 100/125/150/175%. Sizes obey the same rule: **Qt logical length × `dpr` = physical length**, so window/sticker/Pixmap sizes must use logical units (or set `QPixmap.setDevicePixelRatio`); using physical pixels as logical sizes inflates windows and images by `dpr` (e.g. a 2K panel set to 1080 + 125% makes stickers larger than the source image). Runtime display changes are part of the same space: a screen changing resolution or scale, being unplugged, being plugged in, the arrangement being dragged, or the primary screen switching must clear the mapping caches, re-derive every sticker's dpr-dependent size, and move stickers that ended up on a removed screen back into the remaining desktop (`Application.handle_screen_change`); an in-progress capture mask is deliberately not migrated. The consideration space for coordinate or size work is a **cross product, not a single case** — enumerate it: screen count (1–4) × resolution (1080p/2K/4K, including a high-resolution panel driven at a lower resolution) × scale (100/125/150/175%) × arrangement (row, column, grid, diagonal stagger, physical gaps, negative origin, mirrored order) × runtime change (screen added, removed, resolution or scale changed, arrangement dragged, primary switched) × data origin (fresh config, legacy data, session restore with position+size / position only / neither) × the Qt native layout model (positions derived from each screen's own dpr, or compact accumulation — both occur in the wild; layouts Qt would never produce, e.g. overlapping logical rects, are not valid fixtures) × whether the OS scale report is trustworthy (`GetDpiForMonitor` available, or degraded to size+position heuristics) × affected path (capture, capture-to-sticker, sticker creation, restore, loading, cross-screen drag, both editors, notification and recycle-bin thumbnails, magnifier, tray). Generate this product in tests instead of listing examples — `test_display_mapper_full_cross_product_of_screen_variables` covers 40k+ combinations, alongside `test_display_mapper_round_trips_across_screen_dpi_matrix` and `test_display_mapper_survives_screen_add_remove_and_parameter_change`. When touching coordinate code, add the layout to `test_display_mapper_round_trips_across_screen_dpi_matrix` (single 100/150/175%, dual horizontal same and mixed DPI, a 2K panel at 1080 + 125%, dual vertical with negative origin, Qt-native rounding gaps, three-screen and 2×2 four-screen mixes) and run `-k hover`, `-k mask`, `-k uia`.

23. `RELEASE_NOTES.md` entries must be **concrete, never summary-level**. Start every bullet with **修复 / 新增 / 调整 / 移除**, and state: what was observably wrong (or what is new), the file/function or user path affected, the before → after behaviour, and how it was verified (test name or command). A reader must be able to tell exactly what changed without reading the diff — lines like “优化了识别性能” or “修复了若干问题” are not acceptable.

24. **Startup file ownership and reset ordering.** `main()` takes the single-instance lock (`%APPDATA%\ScreenSnap\screensnap.lock`) *before* `Application` runs `apply_version_gate()`, and the logger holds its file open: on Windows neither can be deleted. Any code that clears or replaces the user data directory must therefore (a) tolerate in-use files by skipping them (`shutil.rmtree(..., onerror=...)`) instead of failing the whole operation, and (b) complete its state rebuild even when the delete partially failed — otherwise the stored `app_version` never catches up and every launch retries and reports the same error. Keep the version gate *after* the lock: it must not wipe data while another instance is running. When resetting user data, preserve only environment/habit keys (`PRESERVED_ON_VERSION_RESET` in `config/config_manager.py`, documented in README); appearance, tool defaults and transient state must fall back to the new version's defaults.

25. **Collect recurring mistakes as experience — and ask before writing them down.** Keep a running list of mistakes that actually happened (yours or the project's), especially ones that repeat. When you notice a new class of failure, do **not** silently edit this file: report the finding and **ask the user whether to add it** to the 「经验与常见错误」 section. Each accepted entry needs three parts — **现象** (what was observed, with the exact error/exit code or a symptom like "Ran 0 tests"), **排查** (the concrete steps that located it: which command, which log line, which comparison), and **避免** (the rule or checklist that prevents it next time). Keep the list short and actionable; merge duplicates instead of appending near-copies. Tooling mistakes count as much as product bugs: anchor/indentation misses, silent no-op edits, destroyed files, hung dialogs, backgrounded commands and empty output files are all worth entries when they cost a round.

26. **Enumerate the combination space while thinking, and cover the primary screen as seriously as the secondary one.** The cross product in rule 22 (screen count × resolution, including a high-resolution panel driven lower × scale × arrangement × runtime display change × data origin × Qt layout model × trustworthy or degraded scale report × affected path — the 30k+ combinations generated by the display-mapper tests) is a **reasoning tool, not just a test fixture**. Before writing code and again before reporting, walk those axes explicitly: say which combinations the change can affect, which of them the current implementation already handles, and which you are deliberately leaving out and why. Discovering the same class of omission once per user report is the failure mode this rule exists to stop. The same applies to every other enumerated surface in this guide (both editors, all capture gestures, every notification trigger, every configuration lifecycle stage) — check the whole list, not the one item that was just mentioned.

    Primary vs secondary screens are a first-class axis, not an afterthought. Many features in this project have been found working on the secondary monitor while broken on the primary one (and the reverse): the primary screen sits at the Qt origin, is usually the one with a different scale, and is where the mask is created and where notification and dialog positioning is anchored. For any change to geometry, coordinates, sizes, focus, click-through, hover/UIA, magnifier, stickers, editors, notifications, dialogs, tray or hotkeys, verify on **both** the primary and the secondary screen (plus a mixed-DPI pair and a 2-screen negative-origin layout where relevant), and treat "works on one screen only" as a bug rather than a limitation. State in the report which screens and arrangements were actually exercised, and mark the ones that were not as unverified instead of implying full coverage.

27. **Consider the blast radius of shared code, not just the module you were asked to change.** ScreenSnap reuses a small set of primitives across many entry points — save_image / normalize_save_as_path, copy_saved_to_clipboard, the editor status to notify channel, the mask's per-view state, MaskWindow vs InlineEditor, the app/*_flow.py mixins, and the test stubs — so a change that is correct in the named place regularly breaks a sibling caller. Before editing shared code, list every caller (grep the symbol, the signal and the settings key it touches); after editing, re-verify each of them explicitly, because a fix that only covers the reported entry point is not finished. Two failure modes already seen here: an anchor-based edit that landed in the wrong sibling function because two functions contained the same line, and a notification fix that repaired the copy action while the save action kept double-notifying through the same channel. Report which other callers and modules you checked, and which you did not.

28. **Commit rhythm: the guide first, the round aggregated at the end.** Commit AGENTS.md changes on their own as the round's first commit — this file is explicitly exempt from rule 21, the user has authorised it — so the conventions are in place before the code that follows them. Keep the rest of the round uncommitted while you work and commit it at the end, aggregated into one summary commit; split it into finer feature commits when the user asks for that. RELEASE_NOTES.md is never staged.

## 经验与常见错误

> 由规则 25 维护：只收录**真实发生过**的错误；每条含「现象 / 排查 / 避免」；新增前先询问用户。

### A. 脚本化批量改代码

- **A1 补丁报告已写入，但行为没变**
  - 现象：脚本打印 [OK]，运行结果却与改动前一致。
  - 排查：把锚点原文打印出来逐字符比对 —— 最常见是缩进少或多 4 个空格。
  - 避免：锚点必须包含真实缩进；改前先打印锚点，改后 grep 行号复核。
- **A2 改 A 函数却改到了 B 函数**
  - 现象：另一个功能报 NameError（例如 save_settings 出现在 save() 里）。
  - 排查：git diff 看实际命中位置 —— 裸字符串 replace(count=1) 命中的是第一个匹配。
  - 避免：跨函数出现的同名行不要用裸字符串定位；按行号或函数范围定位，改后核对命中行。
- **A3 文件被写成语法错误**
  - 现象：IndentationError、双逗号、def 缩进丢失。
  - 排查：跑 py_compile，或把异常完整打印出来（不要被输出截断吞掉）。
  - 避免：写盘前先 compile(content, path, exec) 自检，失败就不写。
- **A4 新用例没被收集，Ran 0 tests 却报 OK**
  - 现象：以为用例已存在，实际插入到了类外（首行被 trimStart 去掉了缩进）。
  - 排查：核对 Ran N 是否等于预期条数；用 -k 过滤时 0 条要当失败看。
  - 避免：插入代码不要用 trimStart；插入后按全名运行一次确认被收集。
- **A5 文件被切到错误位置**
  - 现象：文件头部跑到类后面、内容错位。
  - 排查：搜索 class 字符串时命中了用例体内的 class FakeOverlay。
  - 避免：找类或函数边界用行首正则（^class 或 ^def），不要搜裸 class 加空格。
- **A6 一次删除丢掉几十条用例**
  - 现象：test_sticker.py 从 66 条掉到 5 条（真数据丢失，git 里也没有）。
  - 排查：删除脚本的结束锚点没找到时兜底删到了文件尾。
  - 避免：删除前先验证结束锚点存在，不存在就中止；改完核对条目数守恒。

### B. 跨语言脚本与命令执行

- **B1 JS/TS 解析阶段就失败（Expected comma / unicode escape）**
  - 现象：程序什么都没执行就报解析错误。
  - 排查：模板里出现了 markdown 反引号、未转义双引号，或反斜杠 u 转义序列。
  - 避免：跨语言生成代码用占位符替换（例如两个 @ 代表反引号）；或直接写单语言脚本文件。
- **B2 命令结果没有 stdout**
  - 现象：Cannot read properties of undefined (reading text)。
  - 排查：长命令超过执行器上限，被转成后台作业（返回 kind 为 background）。
  - 避免：把命令拆短；拿到 job_id 后用 job_output 收结果；不要假设一定有 stdout。
- **B3 重定向出来的文件是空的**
  - 现象：读回文件为空，误判为程序没有输出。
  - 排查：Start-Process 的 RedirectStandardOutput 在沙箱里写不出内容。
  - 避免：用 python -u 加 *> file 再 Get-Content 读回；仍失败就直接运行看终端。
- **B4 PowerShell 里拼 CRLF 报错**
  - 现象：字符串里的换行被当成表达式。
  - 排查：JS 模板把反引号当成模板分隔符。
  - 避免：用 [char]13 加 [char]10 拼接；或把脚本写成独立文件再执行。

### C. 测试与环境

- **C1 测试进程挂死不返回**
  - 现象：命令被转成后台作业、一直不出结果。
  - 排查：mock 只替换了第一处，其余用例弹出真实模态文件对话框。
  - 避免：替换对话框或补丁点时先 Select-String 数出现次数，按全部替换；跑测试要有超时兜底。
- **C2 复现不出线上现象**
  - 现象：探针跑不出日志重复、UIA 崩溃等真实症状。
  - 排查：离屏平台枚举不到屏幕；纯控制台进程跑 UIA 会直接退出。
  - 避免：复现脚本的环境必须与主程序一致（真机平台加 Qt 上下文）；否则只给静态推导并说明未实测。
- **C3 改了接口，测试桩报 AttributeError**
  - 现象：FakeEditor 没有 copy_done 属性。
  - 排查：测试桩没有跟随新信号或新方法更新。
  - 避免：改接口时同步更新测试桩（规则 16）；桩缺属性能立刻暴露未接线问题，别用 getattr 掩盖。
- **C4 偶发无摘要被当成失败**
  - 现象：模块跑完没有 Ran 或 OK 摘要，退出码非 0。
  - 排查：离屏收尾原生崩溃（同一天同一模块可复现为通过）。
  - 避免：重跑一次并用 Ran N 判定；分模块、必要时分块运行。

- **C5 只在副屏验证，主屏问题被遗漏**
  - 现象：功能在副屏正常，切到主屏就错位或失效（同类问题已被多次发现）。
  - 排查：复现只在单屏或副屏做过；主屏位于 Qt 原点、缩放常与副屏不同，坐标与尺寸走的是另一条分支。
  - 避免：每个功能都在主屏与副屏各验一次（含混合缩放与负原点排布）；报告里写明验证过的屏幕，未验证的标注未验证。

### D. 判断与流程

- **D1 把推断当成结论上报**
  - 现象：报告的问题事后被推翻（例如跨屏清理会污染日志其实不会）。
  - 排查：结论缺少取证 —— 没实测、也没有可核对的引用。
  - 避免：报告前必须取证：实测复现，或给出可验证的代码行号与条件；不确定就标注未证实。
- **D2 擅自改用户没要求的东西**
  - 现象：用户说只是询问，代码却已被改动。
  - 排查：把提问当成了授权。
  - 避免：提问不等于授权；先回答、再问是否动手，改动前明确说出要改哪些文件。
- **D3 文档结论与实现不符**
  - 现象：文档写着已修复，实际仍存在（如日志放大）。
  - 排查：只看了局部代码就下结论。
  - 避免：结论必须来自实测；被推翻时显式更正文档（含修正记录，并说明原结论为何不准确）。
- **D4 同类问题反复出现（只修了报告的那一个入口）**
  - 现象：修完仅复制，保存又报同样的重复通知。
  - 排查：两个动作共用同一条 status 到 notify 的通道，只改了其中一处。
  - 避免：修一处后审计所有同类入口（同一信号或函数的所有调用点），并把结论写进文档防止复发。


## Validation

- Use the project environment's `python` executable. On the expected Windows setup, `py -3` may resolve to a different interpreter without project dependencies.
- The suite is split by feature under `tests/` (shared base: `tests/base.py`): `test_config`, `test_capture`, `test_sticker`, `test_uia`, `test_dpi`, `test_ui`, `test_app`, `test_misc`, plus `tests/editor/` (`test_canvas`, `test_tools`, `test_text`, `test_erase`, `test_zoom`). Run the narrowest relevant module first (e.g. `python -m unittest tests.test_dpi`) and always in its own process: a module that leaves mask/editor/focus state behind fails unrelated later cases, and one shared process also hits the offscreen shutdown crash. There is no `tests.test_core` any more.
- Each test module runs standalone (`python tests/test_app.py`), via `python -m unittest tests.test_app`, or under `discover` from either the repository root or the `tests/` directory; modules bootstrap the repository root onto `sys.path`, so never rely on the caller's cwd.
- Qt widget tests run offscreen in CI and may emit platform clipboard or window warnings. Report test counts and failures from unittest's final summary, not from those warnings.
- Real global hotkeys, mixed-DPI display geometry, UI Automation providers, and click-through behavior require Windows desktop verification when a change affects them.
- The suite is 500+ Qt tests; a single-process run can stall or crash natively at offscreen shutdown. Prefer chunked runs (e.g. 20 tests per subprocess with a timeout) and read a non-zero exit together with its `OK` summary as the known shutdown crash, not a failure. Tests that genuinely need a real desktop screen (window handles, a screen wider than the editor) should be `unittest.skip` placeholders instead of failing.

- `build.bat` and `start up.bat` are stored as **GBK (cp936), no BOM, CRLF** and start with `chcp 936`. cmd.exe mis-parses UTF-8 batch files under `chcp 65001` — multi-byte characters straddling a read block get split and the tail of the line is executed as a command — while a GBK file under a UTF-8 console prints mojibake. Keep both scripts GBK + `chcp 936`, keep `.gitattributes` (`*.bat text eol=crlf`), and decode them as GBK when reading or editing.

## Ownership Map

- `screenshot/mask_window.py`: multi-monitor screenshot surface, selection rendering, selection actions, inline editor lifecycle.
- `screenshot/selection_rect.py`: physical-pixel selection geometry and resize behavior.
- `editor/annotation_canvas.py`: annotation scene, view-only overlays, editing interactions, undo history, and unadorned scene rendering.
- `editor/editor_window.py`: standalone editor behavior and consistent save/copy output.
- `editor/image_effects.py`: final output transformations shared by saving, clipboard, and output preview.
- `config/config_manager.py` and `ui/settings_editor.py`: persistent configuration defaults, validation, and settings controls.
- `app/capture_flow.py`, `app/sticker_flow.py`, `app/notification_flow.py`: the `Application` behaviour split by flow as mixins (capture mask and selection dispatch, sticker creation and panel, notifications and save results).
- `app/bootstrap.py`: Qt application subclass, single-instance lock, uncaught-exception hooks. Re-exported by `main.py` for callers/tests.
- `app/notifications.py`: tray icon drawing, notification bridge. Re-exported by `main.py`.
- `main.py`: the `Application` composition root (mixin assembly, tray, hotkeys, config, dispatch) and process entry point.
- `tests/`: regression coverage split by feature — `base.py` (shared imports, `QApplication` fixture, assertion helpers), `test_config`, `test_capture`, `test_sticker`, `test_uia`, `test_dpi`, `test_ui`, `test_app`, `test_misc`, and the `editor/` subpackage (`test_canvas`, `test_tools`, `test_text`, `test_erase`, `test_zoom`).
- `README.md`: user instructions, developer notes, and chronological adjustment log.
