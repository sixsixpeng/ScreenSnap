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

28. **AGENTS.md is committed only on a separate, explicit request.** Changes to this guide are held back: never bundle them into another commit, and never commit them on your own initiative — rule 21 applies to this file like any other. Commit it (alone, or aggregated with the round, if the user says so) only when the user separately asks for it. This does not change how other code files are handled: they keep following rule 21, split into feature commits when the user asks for that.

29. **Expand the user's shorthand into its full scenario set before investigating.** When the user names an area, treat it as the whole area, not the single instance they happened to hit — and troubleshoot every member of the set, not only the one they pointed at.

    - **编辑 / 窗口编辑** means **both** editor windows: the standalone `EditorWindow` and the capture-local `InlineEditor`. They differ in ownership, sizing, capture source, close semantics and in which signals the application wires up, so a finding about one is not evidence about the other. Inspect both, and when a fix applies to shared behaviour, make it cover both (rule 7).
    - **截图** means every capture scenario, at minimum: **未选择** (the mask is open and nothing is selected — hover highlight, hint bar, magnifier, Escape), **左键拖动选择** then following `capture_after_selection` into the editor or a direct save, **右键选择单个** (right-click a single element/window), **右键选择多个** (right-click collecting several regions), plus the paths that bypass the mask — fullscreen / current monitor / repeat-last-region presets, the quick-sticker gesture (hold the quick-sticker key), 取色 picker mode, the configured quick-save shortcut, and multi-monitor variants where the selection spans or starts on another screen.
    - Apply the same expansion to any other shorthand: **通知** means every notification trigger; **贴图** means create, restore, drag, panel and recycle-bin paths; **设置** means the whole configuration lifecycle; **另存为/保存** means both editors and both write paths.

    State in the report which scenarios you actually checked and which you did not, instead of implying that the named one stands for all of them.

30. **Any change to the settings schema triggers a full configuration audit.** When you add, rename or remove a configuration key — or change its type or defaults — re-check the **whole** lifecycle in the same round, not just the call site you were working on: DEFAULTS, legacy/missing-key initialization, \`validate()\` repair rules, the \`PRESERVED_ON_VERSION_RESET\` list, settings-page controls (and their live-refresh wiring), import/export, reset-to-defaults, the version gate's reset path, every reader of the key, the tests that assert them, and the README tables. Missing one of these is how a new key silently reverts, breaks a reset, or leaves the settings page out of sync — the audit is cheap compared with the round it costs later. Report which lifecycle stages you checked.

31. **Run the three change gates — before, during and after every code change.** Rules 26 and 27 already say "enumerate the blast radius" and "verify on both screens", yet they only fired when the user reported a symptom: three consecutive rounds broke behaviour that had worked before (the `` ` `` rescue, the S dual-semantics, the nudge crash) because nothing forced a checkpoint. Make them mechanical, and do not skip a gate because the fix looks small:

    - **Gate 1 — baseline before touching anything.** Run the focused subset for the areas you are about to touch (e.g. `python -m unittest tests.test_capture -k mask`, `-k nudge`, `-k inline`) and show the result. No baseline means no edit: otherwise a regression can only be discovered by the user, and "it was already broken" cannot be told apart from "I broke it" (C7 comes too late on its own).
    - **Gate 2 — impact list.** Write down every historical behaviour the change can reach, using the rule 26/27 enumeration (primary vs secondary screen, both editors, the six capture states, stickers, notifications, the configuration lifecycle), and for each item *how* it will be verified (test name, key, log line). An empty list means the impact assessment is unfinished.
    - **Gate 3 — same subset afterwards, plus one new regression test.** The same `-k` subset must be green, and the historical behaviour you touched gets a test of its own; if it genuinely cannot be covered, say why. **Never turn red into green with `@unittest.skip`** — a skip is allowed only for something that truly needs the real desktop (rule 19), and the report must then state "coverage removed" for that case.
    - **Report four things:** which entry points changed, which command produced which result, which surfaces were verified, and which were **not** verified.
32. **Any change to a capture/editor state must audit the hint bar in the same round — wording *and* visibility.** The hint bar is where users learn the current gestures, and it drifts silently: after R changed from "recapture" to "clear selection" the hint still read 重新截图; after the picker-mode state was removed, `hint_texts()` kept a whole dead `picker` branch plus a `picker=` argument nobody passes any more; the inline state still says 双击空白提交 although 提交 only exists in multi-select. Whenever a change touches a state, a gesture, or the meaning of a shortcut — or adds/removes a state — do all of the following in the same round:

    - Enumerate the states and write down, for each, which hint items must show, which must be empty, and the exact sentence: **未选择** / **左键选择后（原地编辑）** / **右键选择后** / **多选收集** / **取色** / **两个编辑器**. A change that does not say what the hint bar should look like afterwards is not finished.
    - Update `screenshot/hint_items.py` in the same commit, and update the `-k hint` tests — they are the contract for the wording (C8). Wording changes are contract changes: say which cases changed meaning.
    - Delete items and branches for states that no longer exist: no dead `picker`-style branch, no parameter that is always `False`, no item that can never render.
    - Keep each sentence actionable and unique: never mention an action that cannot be performed in that state, and never repeat one sentence in two items (提交 belongs to multi-select only; the inline state 保存 rather than 提交).
    - Verify the hint bar in the real app on **both screens** (rule 26): offscreen tests cannot see it, so a green suite is not evidence about the hint bar.
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

- **A7 未入库文件被脚本覆盖后无法回滚，且覆盖时无人察觉**
  - 现象：RELEASE_NOTES.md 的内容变成了另一段完全无关的文本（对话里发过的检查清单），而我此前多轮一直在编辑它；发现时已是最后一次写入之后。
  - 排查：read 文件头部 + Select-String '^### ' 核对结构；git ls-files RELEASE_NOTES.md 返回空 → 该文件按规则 20 **从未入库**，没有历史可回滚。
  - 避免：对"故意不提交"的文件（RELEASE_NOTES.md）**每次写入前先复制一份到临时目录**；写脚本时内容变量与目标文件名一一对应，写盘前断言首行等于预期标题（如 「# ScreenSnap 发布记录」）与必要结构标记，不满足就中止。

- **A8 临时脚本名一旦被删过就不能复用（工具限制）**
  - 现象：write 报 cannot write "…\_t2.py": file no longer exists — re-read the file, then retry，整段程序中止，本轮白跑一次。
  - 排查：该文件名在本会话里被 Remove-Item 删过；工具按"已观察到的文件状态"校验，删除后即视为不存在。
  - 避免：临时脚本一律用**新名字**（_fix1.py、_fix2.py… 或带序号/时间戳），不要复用删过的名字；收尾清理只删本次新建的文件。

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

- **C6 模块导入期构造 Qt 对象，进程原生崩溃且无任何输出**
  - 现象：`import core.constants` 直接崩溃，`exit=-1073741819`（0xC0000005 访问违规），**一个字符都不打印**；跑测试模块时只有 exit 码、连 `Ran N` 摘要都没有，极易被当成"已知的离屏偶发崩溃"放过。
  - 排查：用**独立的单行导入自检**（单独脚本文件，不要依赖 shell 里的嵌套引号）确认崩溃发生在导入阶段；看退出码是否为 -1073741819；并确认改动前该模块能否被导入。
  - 避免：模块级只放纯数据与枚举（例如 `QKeySequence.Undo` 枚举值）；`QKeySequence(...)`、QIcon、QPixmap 等 Qt 对象一律在函数内构造，或在 QApplication 建立之后构造。

- **C7 模块变红先用 `git stash` 与 HEAD 对照，别把既有失败算成回归、也别放过自己引入的回归**
  - 现象：跨屏多选改完后 `test_dpi` 3 条红，无法判断哪些是我引入的。
  - 排查：`git stash push <改动文件>` → 在 HEAD 版本跑同样 3 条 → 对比（1 条 HEAD 通过=我引入的回归；2 条 HEAD 同样失败=既有失败）→ `git stash pop`。随后定位到回归原因是归属判定把测试桩 Mock 的 `monitor_rect.contains()` 当真值，加了 `isinstance(monitor_rect, QRect)` 守卫即解决。
  - 避免：改动共享方法后若某模块变红，先做 HEAD 对照再修；引入的回归必须修完才能提交，既有失败顺手修或明确标注（都属于"改了实现没改期望"的爆炸半径，规则 27）。

- **C8 设计契约变更后没有同步旧断言，红的是"旧契约"而不是回归**
  - 现象：把"采集阶段快捷键只在主遮罩上创建"改成"每块遮罩各建一份"后，test_dpi 三条红：assertTrue(all(view.capture_action_shortcuts is None …))、assertIsNone(secondary.capture_action_shortcuts)、以及用例名里的 only_primary_has_shortcuts。
  - 排查：grep -n "capture_action_shortcuts is None|only_primary|is None" 定位旧断言；再用 git stash 对 HEAD 跑同样用例（见 C7）区分"旧契约"与"真回归"。
  - 避免：契约级改动（一份→多份、单窗口→多窗口、单选→多选）先把**断言、用例名、文档表格、日志文案**一起 grep 出来改（本轮还漏改了「S 快捷键未创建…（只有主遮罩视图创建快捷键）」这类旧日志文案）。

- **C9 会话级/全局资源的用例要断言"性质"，不要断言"精确次数"**
  - 现象：AssertionError: ['escape-handle', 'escape-handle'] != ['escape-handle']（关闭路径多同步一次）；把桩改成幂等后仍红，两轮都耗在这条脆断言上。
  - 排查：让断言把**实际列表**打出来（本次就是列表差异一行定位），并检查桩是否幂等（句柄为空应早退）。
  - 避免：对"安装/释放/幂等/去抖"这类资源，断言**性质**——可见期间**不**释放、不可见后**至少**释放一次、不重复安装；桩必须幂等。

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

- **D5 "没反应"类问题先加可观测点，再动逻辑**
  - 现象：主屏 Alt+M 进不去多选，我按推测连改两轮都没中（改归属转交、改快捷键放行），用户只能反复回报"还是不行"。
  - 排查：在入口与每个分支加 DEBUG（`多选切换：视图=… primary=… inline=… _forwarded=…`），一次复现就把链路钉死（日志直接显示"收到请求 → 转交给主视图 → 主视图那一跳被拦下"）。
  - 避免：凡是"按了没反应"的问题，第一轮先补日志（入口 + 分支 + 忽略原因），拿到一次真实复现再改代码；没有任何可观测点的修复只能算猜测，不许当结论上报。

- **D6 防双触发的去抖/幂等守卫会反噬——必须区分"用户重复"与"内部转交/再入"**
  - 现象：为防两条 Alt+M 快捷键各触发一次而加的 0.25s 去抖，把同一毫秒内的**主动转交**也判成重复（日志：`多选切换：忽略 0.000s 内的重复触发`）→ 主屏依旧进不去多选。
  - 排查：去抖时间戳在**入口**就写入，转交发生在同一毫秒；`not _forwarded` 之前的判断把内部调用与用户第二次按键混为一谈。
  - 避免：守卫要带**显式来源标记**（`_forwarded=True` 之类的再入标记）并只对用户入口生效；写去抖时同时写下"哪些内部调用必须绕过它"，并留一条"忽略原因"日志以便区分二者。

- **D7 文档丢失后按"未推送提交"重建，不许凭记忆编造**
  - 现象：发布记录被误覆盖且无历史可回滚，我一度想凭记忆补条目。
  - 排查：git log --oneline gitcode/master..HEAD（未推送＝未发布）逐条列出，映射到发布记录分区；必要时用 git log -S 定位具体改动。
  - 避免：重建只以可核对的事实（提交列表、diff、日志）为依据；查不到出处的分区显式标注"原条目丢失，待补"，不编造（规则 23 同样适用于重建）。

- **D8 输入源"只建一份"是多屏应用的高频缺陷（同一按键被多条快捷键/多个窗口触发）**
  - 现象：某块屏上 Esc/S/C/F/E/R/Alt+M/Y/反引号全部无反应，点一下屏幕就恢复；而且是"每次都要点"而非只首次。
    后续（反面教材）：照"每窗口各一份"改完，问题升级为**主副屏 Esc 全部失效**（1 条日志即可看出：同一键的"已创建"出现多份、而"触发"为 0）。
  - 排查：看快捷键日志里的**视图矩形**（只出现一个视图 ＝ 另一块屏没有键）；看日志行的「前台=」字段是否一直是外部窗口；grep -n "if primary:" 找出所有"只建一份"的输入源；再看兜底守卫（如 _sync_escape_fallback）的判定基准是不是"任意窗口活动"而不是"光标所在窗口活动"。
  - 避免：① **快捷键只保留单一来源**（本项目的 \`if primary:\` 结构），**绝不**为多屏"每窗口各建一份同键 ApplicationShortcut" —— Qt 会判为 ambiguous（冲突）而**拒绝触发任何一份**；本条经验的第一版就是这么写的，照它改完直接把"某块屏不行"升级成"主副屏 Esc 全废"（真机实测）。
    ② 多屏输入的正确解法是**让光标所在那块遮罩成为活动窗口**（core/window_focus.activate_window + 前台兜底），因为 ApplicationShortcut 只要求"本应用是前台"。
    ③ 判定/兜底（含 Esc 兜底是否安装）一律以**光标所在那块遮罩**为基准，不要以"某块遮罩（如 primary）是否活动"为准，也不要用时间戳去抖（见 D6）。
    ④ **可打印字符**（反引号等）会被 QGraphicsView 用 ShortcutOverride 吞掉：遮罩侧的键收不到，需要在**编辑器侧**再建一份（挂 canvas、context 用 \`Qt.WindowShortcut\`，如 Alt+M / 工具栏隐藏键），或把按键显式转交。

- **D9 涉及框架行为的假设先用最小探针证伪**
  - 现象：我断定反引号键无反应是 QKeySequence 解析为空，差点据此改错方向。
  - 排查：写 5 行离屏探针直接打印 QKeySequence(...).isEmpty()/toString()（QT_QPA_PLATFORM=offscreen）→ 实测 isEmpty=False、toString 为反引号，假设被证伪。
  - 避免：凡"框架大概是这样"的判断先探针验证；探针结果与推断不符时把它作为证据写进报告（D1），并据此换方向。

- **D10 关键动作必须分段留痕：入口 / 命中 / 忽略原因 —— 「没反应」先证明输入有没有到程序**
  - 现象：副屏反引号无效。我先给快捷键加「已创建」「就绪」日志，两者都显示 启用=True，却始终没有「触发」；再在遮罩视图的 keyPressEvent 里逐键打印，仍是 0 行 —— 这才明白不是路由问题，而是按键根本没进程序（焦点被别的窗口拿走，或自家的全局键盘钩子吞了输入）。
  - 排查：① 在每一层输入入口都留一条痕：QShortcut.activated / 编辑器事件过滤器 / 遮罩 keyPressEvent / 应用级 QApplication eventFilter；② 对着日志比「前台=」字段与 isActiveWindow() 的结果是否一致；③ 用环境变量临时关掉自家全局钩子（本项目 SCREENSNAP_NO_ESC_HOOK=1）再按同一个键，若立刻恢复即证明是钩子吞输入。
  - 避免：任何关键动作都要三段留痕 —— 入口（收到没有）、命中/判定值（键值、启用状态、视图归属）、忽略原因（为什么没做）；「没反应」类问题先证明输入到没到程序，再谈路由与判定；禁止用「按理应该会触发」当结论（D1/D5）。
- **D11 窗口被系统报为前台，却收不到任何键盘事件 —— 别再往 Qt 输入链加入口，改用不依赖焦点的机制**
  - 现象：副屏快速编辑里按工具栏隐藏键无效。陆续加了 QShortcut（已创建/就绪/触发）、编辑器事件过滤器、遮罩 keyPressEvent 三层日志，全部 0 行；再装**应用级 QApplication eventFilter**，同样 0 行 —— 而同一时刻日志显示「前台=python(2560,0,2560x1440)[本进程]」且 isActiveWindow()=True。同一进程里另一块屏的窗口按键完全正常。
  - 排查：① 先用应用级 eventFilter 区分「键没进程序」与「进了没处理」（D10）；② 用 SCREENSNAP_NO_ESC_HOOK=1 关掉自家全局钩子复测，排除自家人为因素；③ 对比同一操作在两块屏上的日志差异（一块有事件、一块零事件）。
  - 避免：输入类需求不要只押在 Qt 的快捷键/焦点链上；对「两块屏都必须可用」的键，优先采用**不依赖窗口焦点**的机制（本项目做法：用 keyboard 注册全局热键，截图开始时注册、遮罩关闭时释放，钩子线程只 emit 信号、切换在 Qt 线程执行），并保留一个可临时禁用它的环境变量开关便于对照；一旦证据表明系统级输入不可达，就停止在这条链上继续加入口，换机制收尾。
  - **实测收窄（2026-10-10 真机复核）**：副屏上动作键（S/C/F/E/R/Y/Alt+M）全部正常 —— 它们走 Qt 的 ApplicationShortcut，只要求「本应用在前台」，与「哪块遮罩拿到键盘事件」无关；真正必须改用不依赖焦点机制的只有两类：① 可打印字符类自定义键（反引号）；② 方向键/WASD 微调。因此本条的适用范围是这两类，而不是「所有多屏按键」。
- **D12 自由变量在跨屏路径里指向别的 C++ 对象 → 原生崩溃（无 Python 栈）**
  - 现象：遮罩 `keyPressEvent` 里写着 `QCursor.setPos(view.mapToGlobal(view.to_logical_point(self.position)))` —— `view` 本是编辑器事件过滤器里的名字，在遮罩方法里解析到了别的同名对象；副屏（T2752Q）拖手柄微调时命中跨屏/已销毁的 Qt 对象 → **原生访问违规**，日志里只有「原地编辑就绪：视图=(2560, 0, …)」然后**无异常栈直接断掉**。
  - 排查：崩溃日志**没有 Python 栈**时先怀疑 C++ 对象误用（不是 Python 异常）；在该方法区间里搜自由变量：`Select-String 'view\.'` 限定方法行号范围；用 `git log -- <file>` 对照历史版本确认这行原本该用什么。
  - 避免：事件处理/槽函数里一律用 `self`，跨类复制代码后立刻检查自由变量；`QCursor.setPos`、`mapToGlobal`、`to_logical_point` 这类系统调用前加 `isinstance(...)` 与 `isVisible()` 守卫；同一 bug 往往不止一处（本次两处 setPos 都错）。

- **D13 输入类问题先数「入口日志」，再谈判定**
  - 现象：副屏 WASD 无效，我连续两轮按「判定/变量」去改（改门槛、改 setPos、加全局热键）都没中；而入口日志「遮罩收到按键(快速编辑中)」计数是 **0** —— 按键根本没进程序。
  - 排查：入口、命中/判定值、忽略原因三处各留一条日志（D10），**先数入口条数**：入口 0 条 ⇒ 输入没到程序（焦点、全局钩子、系统层拦截）；入口有条数但没命中 ⇒ 才是判定问题；有命中但没动作 ⇒ 看忽略原因。
  - 避免：入口计数为 0 时**禁止**改判定逻辑（改了也不会好，只会引入新回归）；把入口计数写进报告当作结论依据。

- **D14 不要用「吞键式」全局热键去兜底带双语义的键**
  - 现象：为修副屏微调，我把 `w/a/s/d` 注册成 `suppress=True` 的全局热键 → **整个 S 键被系统级吞掉** → `save_selection()` 再也收不到 → 主副屏**保存**同时失效，破坏了 `f719939` 定下的「S 按住控制点=下移微调 / 否则=快捷保存」双语义。
  - 排查：改某个键之前先 `git log -- screenshot/mask_window.py` 找到它的设计提交，用 `git show -s --format=%b <sha>` 读设计意图（本次就是 `f719939` 的提交说明写着 S 走 `save_selection()` 判定）。
  - 避免：兜底热键一律 `suppress=False`（让原路径照常工作），或只兜**没有双重语义**的键（例如工具栏隐藏键）；**绝不**吞掉既是动作键又是修饰性判断的键（S/W/A/D、方向键、Space）；改共享按键语义前先查它当初为什么那样设计（规则 27）。
- **D15 原生崩溃不会进 `sys.excepthook` —— 必须开 `faulthandler`，现场看 `crash.log`**
  - 现象：连续几轮「程序崩溃但日志里一条都没有」，`app.log` 最后一行还停在正常业务日志（如「原地编辑就绪」）；而同一份日志里 Python 异常（`10:58 未捕获的全局异常`）却有完整记录 —— 说明日志器本身没坏。
  - 排查：先看退出码 —— `-1073741819`(0xC0000005 访问违规) / `3221225477` / `-1073740940`(0xC0000374 堆损坏) 都属**原生**崩溃；`logging`、`try/except`、`sys.excepthook`、`threading.excepthook` 在这些情况下**一律不会执行**；手工复现时加 `python -X faulthandler -m unittest …` 即可拿到 Python 栈（本次正是靠它把崩溃钉在 `QTest.keyClick` 那一步）。
  - 避免：启动即 `faulthandler.enable(file=<logs>/YYYY-MM/crash.log, all_threads=True)`（本项目已接入 `logger/log_setup.py` 的 `enable_crash_dumps`）；排查顺序固定为 ①`app.log` 最后一行 → ②没有 Python 栈 ⇒ 看 `crash.log` → ③按栈定位；**禁止**再用「改一版试试」代替取证。

- **D16 别用 `QKeyEvent.keyCombination()` 处理可能由测试/合成的按键事件**
  - 现象：为处理可打印字符，我在遮罩 `keyPressEvent` 里加了 `QKeySequence(event.keyCombination()) == 序列` 判定；随后 `QTest.keyClick`（以及真机投递）触发**原生访问违规**，崩溃点在 Qt 事件派发内部，连该分支的入口日志都没来得及写。
  - 排查：删掉该判定后，原先崩溃的用例立即转绿（`Ran 2 tests / OK`）；`faulthandler` 的栈指向 `QTest.keyClick` 那一行 —— 说明崩在事件被投递/处理的过程中，而不是我们后续的业务代码。
  - 避免：可打印字符的快捷键**不要**在 `keyPressEvent` 里做 `keyCombination()` 比较；改用 QShortcut（挂 canvas、context 用 `Qt.WindowShortcut`）或全局热键覆盖；确实要在事件里比较时，先 `isinstance(event, QKeyEvent)`，并只用 `event.key()` / `event.modifiers()` 这类稳定 API，**绝不**对事件对象做序列化转换。
- **D17 “看似冗余”的守卫不能删——理顺后必须在代码位置留主注释**
  - 现象：原地编辑的微调门槛里有 `resizing is None / dragging is None` 两项，我判断它们多余（理由是下面已有 `releaseMouse()` 兜底）并删除 → 用户按住左键拖动标注时按方向键，**画布上的标注全部消失**。
  - 排查：关键就在同一段代码 —— 那两项挡住的是“按住左键拖动/缩放过程中按方向键”这条路径；下方的 `releaseMouse()` 不是等价兜底，而是**强行结束拖动**。回滚（`git checkout -- screenshot/mask_window.py`）后不再丢标注，据此确认是本次改动引入。
  - 避免：① 删除任何守卫前，先写出它拦住的**具体操作序列**（按住什么、按了什么、处于什么状态），写不出来就不许删；② 结论理顺后必须写成**代码位置的主注释**（含“移除后会怎样”与实测日期），只记进 AGENTS 不够 —— 下一轮没人会先读 AGENTS 再动那一行；③ 涉及拖动/缩放/事件中断这类状态机的改动，改前改后都要在**真机**跑一遍操作序列（离屏用例覆盖不到）。

- **D18 探针/用例自己占着资源，却把被测函数的失败当成产品缺陷**
  - 现象：为验证「旧通知身份自动迁移」，探针用 pywin32 往临时 `.lnk` 写完旧的 `System.AppUserModelID` 后立刻调用 `ensure_registered()`，函数返回 `unavailable`（迁移失败），我据此以为产品逻辑有洞。
  - 排查：把探针改成写完 `del store` + `gc.collect()` 释放 `IPropertyStore` 句柄后，同一次调用立刻返回 `created` —— 是探针自己占着文件；另外 Shell 的属性存储**写完立刻读回会给出陈旧值**（读回 `None`），据此判定同样会把成功误判成失败。
  - 避免：① 写 `.lnk`/注册表/COM 属性的探针与用例，操作完必须释放句柄（`del` + `gc.collect()`）再调用被测函数；② 迁移/写入类逻辑**不要用「写完立刻读回」当判据**，以「写入未抛异常」为准，读回只做留痕；③ 探针失败时先怀疑探针自己的环境与资源占用，再怀疑产品。

- **D19 启动早期的留痕写进了还没配置 handler 的 logger，等于没写**
  - 现象：首次运行注册 Windows 通知身份的功能**明明生效**（快捷方式与 AUMID 都在），但 `app.log` 里搜不到那行「已注册通知身份」，排查时一度以为功能没跑。
  - 排查：调用点在 `program = Application()` **之前**，而日志是 `Application.__init__` 里才 `configure_logging`；`logging.getLogger("screensnap")` 此时没有任何 handler，`INFO` 直接被丢弃（Python 的 lastResort 只处理 WARNING 及以上）。把调用移到 `Application()` 之后，同一行立刻出现在 `app.log`。
  - 避免：任何「启动阶段」的留痕都要先确认日志已配置（本项目：放在 `Application()` 之后，或显式 `configure_logging` 之后再记）；写完留痕**必须真机看一次日志**再宣布完成 —— 否则排查时会因为「没有证据」而误判功能未生效。

- **D20 事件路径里遍历 Qt 部件 / 取原生句柄，碰到已析构的 C++ 对象 ⇒ 无 Python 栈的原生崩溃**
  - 现象：连续快速创建并拖动贴图（实测 4 张）时进程直接消失，`app.log` 最后一行停在正常业务日志（`sticker_item:682 停止窗口跟随`），没有任何 Python 异常；`crash.log` 的栈顶是 `core/window_snap.py:141 ignored_app_window` ← `:190 collect`（**ctypes 的 EnumWindows 回调**）← `:209 visible_targets` ← `sticker/sticker_item.py:468 begin_snap_session` ← `:407 mousePressEvent`。同一类崩溃第二次出现时栈顶变成我刚写的新函数 `:148 overlay_handles`。
  - 排查：① `crash.log` 是**追加**写的，`Get-Content -Tail` 看到的是被截断的尾部 ⇒ 必须先按 `Current thread` / `Windows fatal exception` 标记定位**最后一次**崩溃块再读；② 栈顶若是 `winId()` / `effectiveWinId()` / `property()` 这类 Qt 调用，先怀疑**已析构对象**而不是业务逻辑；③ 用 `shiboken6.isValid(widget)` 写 5 行探针确认存活；④ **第一版只加 `try/except` 无效**（第二次崩溃的栈顶正是新加的 try 内部那行）⇒ 证明对已析构对象，`property()/winId()` 是在 **C++ 层**崩的，Python 的 `except` 根本轮不到。
  - 避免：① 遍历 `QApplication.topLevelWidgets()` / `allWidgets()`，或调用 `winId()/effectiveWinId()/property()` 之前，**先 `shiboken6.isValid(w)` 判活**（本项目 `screenshot/mask_window.py::intruding_windows` 的 `live_widget()` 早就是正确写法，照它抄）；② **ctypes 回调里绝不碰 Qt** —— 回调需要的信息要在 `EnumWindows` **之前**收集成纯 Python 数据（`set`/`list`，如 `overlay_handles()`），回调里只做整数比较；③ `try/except` 只能当**第二道**防线，不能当唯一防线；④ 出一处就**全库排查同类点**（`grep -n "topLevelWidgets|allWidgets|winId()"`），本次就是这样又补上了 `core/window_focus.py::widget_handle`；⑤ 改完必须在**真机**复现原操作序列（离屏用例覆盖不到“部件正在销毁”的时序），并为两条分支各留一条回归用例（`isValid=False` 与“取值抛异常”）。

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
