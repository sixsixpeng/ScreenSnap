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
33. **一轮之内把活干完——禁止留下半成品。** 这是本项目反复出现的交付缺陷：一轮里改了配置键却没接线、加了快捷键却没接处理函数、把设置页改出语法错误就停手、或者把"修 A、修 B"拆成四五轮等用户催。**交付前的三问**，任何一问没过就不算完成：

    - **能编译吗** —— 改过的每个 `.py` 都过一遍 `py_compile`；有红就是**没做完**，不许以"下一步再修"结尾。
    - **用例过吗** —— 至少跑**受影响的模块/子集**并给出条数与结论；改了配置键必须跑 `test_config -k every_setting_has_an_entry` 与断言默认值的用例。
    - **接线完整吗** —— 新配置键要有**读取点**（`grep -n "<key>"` 除 DEFAULTS/校验外还有生产代码 ✓）、新信号要有 `connect` ✓、新快捷键要有处理函数 ✓、新 UI 控件要能落到配置 ✓；"只有键没有行为"一律视为未完成。

    **一轮做不完时的正确做法**：要么**不做**（先说清楚），要么把**最小可用闭环**做完（键 → 接线 → 默认值 → 用例 → 文档 ✓）再交付，剩下的按**明确清单**列出并立刻继续，而不是停在"半接线/编译错"的状态。**绝不允许**以 `compile=1`、孤儿续行、未消费的标记变量、无用的配置键收尾。

    这一条同时约束**报告口径**：只报"已实测通过"的部分，未完成项必须逐条点名（含文件与症状），不得用"基本完成/应该可用"含糊带过。

34. **"最终总检查"= 对当前全部未提交/未推送改动做全量复盘，并把配置生命周期完整过一遍。** 当用户要求做最后的总检查（或说"整体检查一下/收尾"），**且全量测试已经跑过**时，不要只复述测试数字就结束：必须针对**当前工作区全部未提交 + 本地未推送的提交**做一次系统复盘，并按下面的清单**逐项给出结论**（有问题的点名到 文件:行号 + 症状 + 证据；没问题的也要写"已查 ✓"）：

    - **A. 改动面复盘（防误伤）**：`git status` + `git diff --stat` + `git log --oneline @{u}..HEAD` 列出全部改动；**逐文件、逐函数**读 diff，对每个改动问三句：① 谁还会调用它（`grep` 符号/信号/配置键，规则 27 爆炸半径）？② 两个编辑器（`EditorWindow` / `InlineEditor`，规则 7）是否都覆盖？③ 主屏与副屏、混合 DPI、负原点排布是否都成立（规则 26）？把"检查过的入口"和"没检查的入口"分开列。
    - **B. 既有 bug 排查（防引入/防放大）**：对每处改动写出它可能触及的**历史行为**以及验证方式（规则 31 的 Gate 2/3）；明确回答"这次改动会不会让某个原本能用的路径失效"，并给出对应的 `-k` 子集与结果。
    - **C. 配置生命周期全面复核（规则 30 的展开，**有新增/改名/删除配置键时逐键过一遍**）**：
      ① `DEFAULTS` 默认值（含注释说明）② 遗留/缺失键的初始化与迁移（是否**幂等**、是否是**一次性闸门**、会不会把用户的取消勾选/排序/自定义改回去并回写磁盘 ✗）③ `validate()` 校验与 `repair()` 修复 ④ 快捷键冲突检测表 ⑤ `PRESERVED_ON_VERSION_RESET`（升级保留清单）⑥ 版本闸门的**重置/恢复默认/回滚**路径（会不会误删用户数据、能否在删除失败时仍完成重建，规则 24）⑦ 导入/导出（导出含新键 ✓、导入旧文件时缺键如何补 ✓）⑧ 设置页控件 + **实时刷新接线**（改了设置当下是否生效，而不是重启才生效）⑨ **每一个读取点**（`grep -n "<key>"` 除 DEFAULTS/校验外必须在生产代码里有消费 ✓ 否则就是"只有键没有行为"✗ 规则 33）⑩ 相关用例（含默认值断言与 `every_setting_has_an_entry`）⑪ `README` 配置表与「按键习惯/不保留」清单。**新增的键要 ①–⑪ 全部走一遍**，任何一格缺失都要在报告里点名。
    - **D. 提交面梳理**：列出未推送的提交与未提交的文件，给出**按 feature 分组的提交建议**（含 AGENTS 需单独提交，规则 28），但**不擅自提交** ✗（规则 21）。
    - **E. 报告口径**：已实测通过 / 未验证 / 已知未完成，三者分开写清楚；禁止用"基本没问题/应该可以"糊过去（规则 33）。
35. **配置键必须"一一对应"，且每次涉及配置的改动都要做一次全量对照；两个本应独立的键绝不允许被暗中联动。** 这条来自一次真实事故：`config/config_manager.py` 里曾有一张 `LINKED_FILL_COLORS` 联动表 + 助手 `fill_colors_following_stroke()`，改「矩形/椭圆线条颜色」时会把「填充颜色」**自动改成同一个值** ✗ ⇒ 用户最终发现"填充色与边框色变成了一个值，之前是分开的" ✓（两个设置项在界面上明明是两项 ✓）。要求：

    - **一一对应**：每一个 `DEFAULTS` 键都必须同时满足 —— ① 有设置页控件（或明确属于"记忆状态"类并在报告中点名 ✓ 例如 `last_capture_rect` / `save_as_dir` ✓）；② 至少有一个**生产读取点**（不是只出现在 DEFAULTS / validate / 设置页 ✓）；③ 在 README 配置表中可查（默认值 + 取值范围 ✓）；④ `validate()` 覆盖其类型或取值域 ✓。任一条缺失都要在报告里点名 ✗。
    - **每次涉及配置的改动都跑一次全量对照**（新增/改名/删除键、改默认值、改校验、改设置页、改联动逻辑均算 ✓）：用脚本遍历 `DEFAULTS`，逐键检查上面四条，并额外报告"默认值相同 / 名称成对（如 `X_color` 与 `X_fill_color`）"的键组，逐一确认它们**本应独立**还是**确实联动** ✓。
    - **禁止暗中联动**：不得让改 A 键的代码顺手改 B 键 ✗（无论叫"跟随""同步""默认一致" ✓）。若产品上确需联动，必须**显式**满足：在 README 的配置表与调整记录里写明"改 A 会同时改 B" ✓ + 在设置页该控件的说明里写明 ✓ + 有用例钉住联动条件（例如"仅当 B 仍等于旧 A 时才跟随" ✓）。否则一律按独立处理 ✓。
    - **报告口径**：每次配置相关改动的报告都要写清"跑了哪些键、发现哪些不一致、修了什么、还剩哪些有意为之的例外" ✓。

36. **所有测试必须有时间限制、可见计时和超时回收，禁止无限死等。** 测试、最小探针、导入自检及后台验证都适用；不能只在说明里写“有超时”，执行器必须真的实施：

    - **启动前显示**：绝对解释器路径、绝对工作目录、完整命令、开始时间、单进程超时秒数；批量任务还要给出整体时间预算。先核实依赖可导入，不假定后台终端继承前台的 cwd 或 Python。
    - **有界预算**：默认定向子集 120 秒、完整模块 180 秒、单模块导入 20 秒；已有耗时证据表明需要更长时，运行前明确提高数值并说明原因，绝不设为无限。每次重试也受期限约束，且受批次整体预算限制。
    - **等待期间可见**：长任务至少每 10–15 秒输出当前模块/阶段、已耗时与剩余时间；使用无缓冲输出，必要时把进度写入可读取的临时日志。后台任务没有新输出也不能无期限轮询；等待后台结果的上层同样要有截止时间。
    - **超时立即处理**：标记 `TIMEOUT`，保留已经产生的 stdout/stderr、命令和日志路径；终止并回收本次测试的进程树，再有限等待退出。Windows 可按本次 PID 使用 `taskkill /PID <pid> /T /F`；清理也要有超时，不得批量杀掉用户的其他 Python/应用进程。禁止测试跑着时继续编辑它所读取的生产代码或用例。
    - **结束必须显示**：结束时间、实际耗时、退出码、`Ran N` 与 `OK/FAILED`。`Ran 0` 不算有效验证；已知原生退出异常即使有 `Ran N / OK` 也单列“摘要通过、收尾异常”，不能说正常退出；无摘要、超时或未知非零退出均不得算通过，只允许有界重试/分块定位。
    - **不因提交检查而例外**：提交前验证也遵守本规则。测试脚本若没有子进程超时，必须由满足本规则的外层逐模块执行器托管；仅给工具设置后台标志或打印一个时间戳不等于有超时保护。

- **D35 绘制期绝不改可见性（子控件与场景图元都不行）**
  - 现象：给"全屏截图松开后尺寸标签消失"做修复时，我在 `drawForeground`（绘制期）里直接 `show()` 一个子控件 `QLabel` ✗ ⇒ `tests.test_capture -k mask` 由 **Ran 22 OK** 变成**摘要前原生崩溃**（`exit=-1073740791` / `0xC0000409` ✓ 无 Ran ✓）。另一次同族隐患：`_paint_erased_composite` → `_cached_annotation_layer` 在绘制期 `base.hide() … scene.render() … base.show()` ✗（改的是**场景图元**可见性 ✓ 同样属风险族 ✓）。
  - 排查：`git stash push <改动文件>` 后跑同一子集（HEAD 版本 **Ran 22 OK** ✓）⇒ 确认是本次改动触发 ✓（C7）；把 `show()` 换成 `QTimer.singleShot(0, …)` 后同子集恢复 **Ran 22 OK** ✓。
  - 避免：① 绘制函数（`paintEvent` / `drawForeground` / `drawBackground` / `paint` / `paintGL`）里**只允许读**，一律不得 `show/hide/setGeometry/setText/raise_/setVisible` ✗；② 需要改子控件就**推迟到事件循环下一轮**（`QTimer.singleShot(0, …)` ✓），并且**只在状态真的变化时**排一次 ✓（否则每帧排队 ⇒ 异常/重绘刷屏 ✓ 本会话真发生过 ✓）；③ 绘制期只做纯读计算，把"目标状态"算好交给延迟回调执行 ✓；④ 改完必跑该模块子集（本例 `-k mask` / `test_uia -k badge` ✓）确认仍是 `Ran N / OK` ✓。
- **D36 遮罩侧代码不得引用画布专属属性（跨类属性误用）**
  - 现象：真机日志被 `AttributeError: MaskWindow object has no attribute erasing` ✗ 刷满（`bootstrap` 的"未捕获异常"成百上千条 ✓）—— 我在遮罩侧写了 `and not self.erasing` ✗，而 `erasing` 属于画布 `AnnotationCanvas` ✓。
  - 排查：`grep -n "self.erasing" screenshot/mask_window.py` 命中非注释行即可定位 ✓；AST 扫描"`self.X` 被读取但本类及项目内基类从未定义"也能提前发现 ✓（注意 `app/*_flow.py` 的 **Mixin** 属性由组装类提供 ✓ 规则 16 ✓ 属误报，需跳过 ✓）。
  - 避免：① 写 `self.X` 前先确认 X 属于**当前这个类**（画布属性只能在 `AnnotationCanvas` 里用 ✓ 遮罩用 `session`/`canvas` 去取 ✓）；② 真机报错一律**先看 traceback 最后一帧的文件与行号** ✓ 本类问题一眼可辨 ✓；③ 同一模式在本会话出现过两次（`erasing` 与更早的 `view` 自由变量 D12 ✓）⇒ 跨类复制代码后逐行核对 `self.X` 归属 ✓。

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
- **A9 拿 read 的「显示文本」当锚点 —— 锚点不存在或误伤别处**
  - 现象：`edit` 报 `old_string was not found`；或锚点看起来一模一样却命中了另一行。2026-10-11 给设置页 tooltip 换行时连续两次因此失败：屏幕上的行被截断到约 118 字符，我照抄它当锚点。
  - 排查：把锚点长度与被改文件的实际行长度打印出来比对（实际 L575 长度=286，屏幕上只有 118）；或直接取 read 返回的 lines[i].text 本身。
  - 避免：锚点一律取程序里的完整 read().lines[i].text（或一段足够长且唯一的片段），绝不照抄终端、日志或对话里被截断的行；改完用 grep -n 复核命中行号。

- **A10 重排「多行拼接字符串」时按句子重写 —— 丢内容 + 语法错**
  - 现象：给一条 286 字符、内部含 4 个换行转义的物理行做换行后，py_compile 报错，后半段说明文字被整段丢掉（该 tooltip 的后 3 句消失）。
  - 排查：git diff --stat 看增删行数（正确应为 N 行到 N 行、仅拆分）；用 ast 取出该 Call 的字符串值，与改后逐字比较（本次一比就看出少了 3 句）。
  - 避免：重排只在已有的换行边界拆物理行，拼接后的值必须完全不变；改前用 ast 记下原值、写盘后断言相等；一次只处理一个字符串（用节点的 lineno / col_offset / end_lineno / end_col_offset 精确定位，不要按看起来像的整行替换）；改坏的第一动作是 git checkout -- <文件> 回到已提交版本。

### B. 跨语言脚本与命令执行

- **B1 JS/TS 解析阶段就失败（Expected comma / unicode escape）**
  - 现象：程序什么都没执行就报解析错误。
  - 排查：模板里出现了 markdown 反引号、未转义双引号，或反斜杠 u 转义序列。
  - 避免：跨语言生成代码用占位符替换（例如两个 @ 代表反引号）；或直接写单语言脚本文件。
- **B2 后台命令的输出、工作目录与解释器不能沿用前台假设**
  - 现象：命令输出缺失时曾报 `Cannot read properties of undefined (reading text)`；2026-10-14 截图贴图基线在后台批量报 `ModuleNotFoundError: No module named 'tests.test_capture'` 等，尚未运行任何真实用例。
  - 排查：前者是长命令被转成后台作业；后者核对运行目录和 `sys.executable`，发现后台落在仓库根且解析到另一套 Python，固定项目目录与已验证的解释器后，原 12 个子集共 105 条均有 OK 摘要。
  - 避免：子进程显式传绝对 `cwd` 和绝对 Python 路径，不依赖前台 `Set-Location`、PATH 或虚拟环境激活；先做依赖/模块导入检查。取得后台任务 ID 后按规则 36 有界取结果；空输出不等于成功、导入失败不等于产品回归。
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
  - 避免：替换对话框或补丁点时先 Select-String 数出现次数，按全部替换；所有测试按规则 36 显示计时并实施超时，超时保留现场、回收本次进程树。停止无限等待后再查遗漏的模态入口，不靠继续等或连续启动新任务碰运气。
- **C2 复现不出线上现象**
  - 现象：探针跑不出日志重复、UIA 崩溃等真实症状。
  - 排查：离屏平台枚举不到屏幕；纯控制台进程跑 UIA 会直接退出。
  - 避免：复现脚本的环境必须与主程序一致（真机平台加 Qt 上下文）；否则只给静态推导并说明未实测。
- **C3 改了接口，测试桩报 AttributeError**
  - 现象：FakeEditor 没有 copy_done 属性。
  - 排查：测试桩没有跟随新信号或新方法更新。
  - 避免：改接口时同步更新测试桩（规则 16）；桩缺属性能立刻暴露未接线问题，别用 getattr 掩盖。
- **C4 分开记录摘要通过、原生收尾异常和无摘要，不能混为全绿**
  - 现象：离屏测试有 `Ran N / OK` 却以原生异常退出；另有贴图用例在摘要前访问违规，只有退出码 `3221225477`。将两种情况混为“已知收尾异常”会把未验证的用例放过。
  - 排查：同时核对完整摘要、退出码和 faulthandler 栈；2026-10-14 `test_sticker_without_saved_position_centers_using_logical_size` 当前版与临时 HEAD 单跑均无摘要，不能用其他 92 条的 OK 代替它。
  - 避免：按规则 36 记录时限与实际耗时；有 `Ran N / OK` 的已知原生收尾异常单列，无摘要/超时不算通过。有限重跑后分块或单跑定位，禁止新增 skip、删除用例或只报通过总数掩盖未验证项。

- **C5 只在副屏验证，主屏问题被遗漏**
  - 现象：功能在副屏正常，切到主屏就错位或失效（同类问题已被多次发现）。
  - 排查：复现只在单屏或副屏做过；主屏位于 Qt 原点、缩放常与副屏不同，坐标与尺寸走的是另一条分支。
  - 避免：每个功能都在主屏与副屏各验一次（含混合缩放与负原点排布）；报告里写明验证过的屏幕，未验证的标注未验证。

- **C6 模块导入期构造 Qt 对象，进程原生崩溃且无任何输出**
  - 现象：`import core.constants` 直接崩溃，`exit=-1073741819`（0xC0000005 访问违规），**一个字符都不打印**；跑测试模块时只有 exit 码、连 `Ran N` 摘要都没有，极易被当成"已知的离屏偶发崩溃"放过。
  - 排查：用**独立的单行导入自检**（单独脚本文件，不要依赖 shell 里的嵌套引号）确认崩溃发生在导入阶段；看退出码是否为 -1073741819；并确认改动前该模块能否被导入。
  - 避免：模块级只放纯数据与枚举（例如 `QKeySequence.Undo` 枚举值）；`QKeySequence(...)`、QIcon、QPixmap 等 Qt 对象一律在函数内构造，或在 QApplication 建立之后构造。

- **C7 模块变红先用工作区外临时 HEAD 副本对照，区分既有失败与新回归**
  - 现象：跨屏多选改完后 `test_dpi` 3 条红，无法判断哪些是我引入的。
  - 排查：`git stash push <改动文件>` → 在 HEAD 版本跑同样 3 条 → 对比（1 条 HEAD 通过=我引入的回归；2 条 HEAD 同样失败=既有失败）→ `git stash pop`。随后定位到回归原因是归属判定把测试桩 Mock 的 `monitor_rect.contains()` 当真值，加了 `isinstance(monitor_rect, QRect)` 守卫即解决。
  - 避免：历史 stash 流程仅作为取证记录，不作为现行默认做法。先用工作区外临时 HEAD 副本（如 `git archive`）在同解释器、平台变量、命令和超时下对照，不 stash/checkout 覆盖用户当前改动。2026-10-14 配置放大镜失败与贴图无摘要在两边复现，而截图两边均 115 OK，据此分别归类。新回归必须修完；既有失败或未验证项明确点名，比较结束只清理本轮临时副本并保留报告。

- **C8 设计契约变更后没有同步旧断言，红的是"旧契约"而不是回归**
  - 现象：把"采集阶段快捷键只在主遮罩上创建"改成"每块遮罩各建一份"后，test_dpi 三条红：assertTrue(all(view.capture_action_shortcuts is None …))、assertIsNone(secondary.capture_action_shortcuts)、以及用例名里的 only_primary_has_shortcuts。
  - 排查：grep -n "capture_action_shortcuts is None|only_primary|is None" 定位旧断言；再按 C7 用工作区外临时 HEAD 副本跑同样用例，区分"旧契约"与"真回归"。
  - 避免：契约级改动（一份→多份、单窗口→多窗口、单选→多选）先把**断言、用例名、文档表格、日志文案**一起 grep 出来改（本轮还漏改了「S 快捷键未创建…（只有主遮罩视图创建快捷键）」这类旧日志文案）。

- **C9 会话级/全局资源的用例要断言"性质"，不要断言"精确次数"**
  - 现象：AssertionError: ['escape-handle', 'escape-handle'] != ['escape-handle']（关闭路径多同步一次）；把桩改成幂等后仍红，两轮都耗在这条脆断言上。
  - 排查：让断言把**实际列表**打出来（本次就是列表差异一行定位），并检查桩是否幂等（句柄为空应早退）。
  - 避免：对"安装/释放/幂等/去抖"这类资源，断言**性质**——可见期间**不**释放、不可见后**至少**释放一次、不重复安装；桩必须幂等。

- **C10 改完文件、以及做全面检查时，都要主动做一次「全量导入自检」，防止误删（文件或 import 行）✗**
  - 现象：改动一轮之后，问题常常出在"**别人**"身上 —— ① 某个文件被脚本误删/覆盖后没人发现（A6/A7/A10 都是这一类）✗；② 某个模块少了一行 import ⇒ 只有真正跑到那行时才 `NameError`（本会话真实发生两次：`sticker/sticker_manager.py` 少了 `root` ⇒ `persist_clipboard_history` 一调用就崩 ✗；`editor/annotation_canvas.py` 少了 `QPoint` ⇒ 新写的指针跟随一跑就 `NameError` ✗）。而"只跑受影响的 `-k` 子集 + `py_compile`" **发现不了**这两类 ✗ —— py_compile 只查**指定文件**的语法，不查它引用的名字与文件是否存在 ✓；子集也只覆盖它自己那条路径 ✓。
  - 排查：三件事一起看 —— ① `git status --short --untracked-files=all` 与 `git diff --stat` ✓（出现 ` D <file>` 或意外消失的文件就是误删 ✗）；② **逐个模块导入**自检：把 `app/core/editor/screenshot/sticker/ui/config/logger` 与 `main` 下所有 `.py` 转成模块名，**每个模块用一个独立进程**导入（这样 C6 那类"导入期原生崩溃"只会打断那一个 ✓ 不会掩盖其余 ✓）：`.venv\Scripts\python.exe -c "import importlib,sys; importlib.import_module(sys.argv[1])" <模块名>` ✓；③ 导入失败/退出码非 0 的模块逐个读 traceback 最后一帧 ✓（在产品代码 ⇒ 真 bug ✓ 按 D21 修产品 ✓，不要先改测试 ✓）。
  - 避免：把"全量导入自检"固定成一轮改动的**收尾动作**（与 `py_compile` 同级 ✓），尤其在：批量脚本改过多个文件之后 ✓、删除/重命名过文件或函数之后 ✓、以及规则 34 的"最终总检查"里 ✓。报告里要写清"自检覆盖了多少个模块、有几个失败、失败的是谁" ✓ —— 只报"子集全绿"不算覆盖到这一层 ✗（本会话多次吃过这个亏 ✓）。

- **C11 直接导入别的 TestCase 会被 unittest 重复收集**
  - 现象：2026-10-14 新增 `tests.test_sticker_lifecycle` 本应只运行14条，却输出 `Ran 36 tests`；模块直接导入了 `ClipboardOutputTests`，既有22条也被收集，造成重复执行和条数误判。
  - 排查：核对测试类定义与 `Ran N`，查看模块顶层导入；改为导入 `tests.test_sticker` 模块后，通过模块属性复用 `output_context`，同一命令恢复为14条。
  - 避免：只复用测试助手时导入模块，不把其他 `TestCase` 类暴露在当前模块顶层，也不通过继承整套测试类复用夹具；新增测试先核对预期条数及完整测试名，不能把重复条目计为新增覆盖。

- **C12 项目根已在 sys.path 里但不在首位，tests/editor 遮蔽生产 editor**
  - 现象：2026-10-14 从 `tests` 目录直接运行 `test_sticker_lifecycle.py`，在收集前报 `ModuleNotFoundError: No module named 'editor.image_effects'`，没有 `Ran N`；同一模块的 `python -m unittest` 入口却能运行。
  - 排查：核对绝对 cwd 和 `sys.path` 顺序：项目根已由 `PYTHONPATH` 加入，但脚本目录在首位，`tests/editor` 被解析为生产包；`if root not in sys.path` 因根已存在而不再前置。移除已有同值条目再将根插入索引0，原目录/原命令恢复14条 OK、退出0。
  - 避免：测试入口必须保证生产项目根在 `sys.path[0]`，不能只保证“存在”；对带 `editor` 等同名子目录的测试树，同时验证模块入口与从 tests 目录直接运行。导入失败先核对模块来源及路径顺序，不以改生产导入、添加 skip 或换 cwd 掩盖遮蔽。

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
  - 现象：修完仅复制，保存又报同样的重复通知；2026-10-14 Space 与两个编辑器贴图均漏复制，快速贴图保存还会重复添加输出效果。
  - 排查：前者共用 status 到 notify 通道；后者逐层追到 `copy_to_clipboard=False` / `save(automatic=True)`，并核对贴图与保存是否各自再次合成，发现问题横跨多个主动入口。
  - 避免：一次生成成品供保存/复制/贴图共用，副作用放在明确用户动作的收尾，不塞到底层 add、缓存写入或恢复。列全左右键/多选 Space、两个编辑器按钮/按键、文件/历史/剪贴板/面板、保存/另存/显式复制的入口矩阵；覆盖四种复制组合、取消、占用、保存失败及通知次数。遵守已确认的图片/路径文字格式边界，无有效路径不借用上一张文件；保存成功与复制成功分别判断，并把例外写进文档。

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
    ⑤ **同一按键的 ambiguity 是「整个应用」级别的**：不只是"每窗口各一份同键 ApplicationShortcut"；**一个窗口里的两份同键**（如工具栏 + 画布各一份）同样会让**全应用的同键全部失效**，连别的窗口上的 ApplicationShortcut 也被拖累。定案证据（2026-10-11）：主屏按 Space 时入口日志 **0 条**（连遮罩入口都没有）；把工具栏那份删掉、只留画布那份后仍为 0 条 ⇒ 说明该键被画布用 ShortcutOverride 吞掉，最终改为**在画布 keyPressEvent 里直接处理**（Space 本来就在那里用于拖动画布，证明它收得到）才彻底解决。

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

- **D21 脚本化改代码时删掉/挪走变量，留下孤立引用 ⇒ 生产代码 NameError，却被当成"测试旧契约"连改三轮测试**
  - 现象：把剪贴板历史图片从 `%APPDATA%` 迁到保存目录时，脚本删掉了 `sticker/sticker_manager.py::persist_clipboard_history` 里的 `root = data_dir()`，而 20 行后仍有 `path = root / "clipboard_history.json"` ⇒ 该方法一调用就 `NameError: name 'root' is not defined`（**生产代码**，不是测试问题）。它表现为 5 条 ERROR + 1 条 FAIL（`test_clipboard_history_persists_across_manager_restart` / `test_rebuildable_cache_cleanup_honors_scope_flags` / `test_sticker_session_and_closed_item` 等），我却按「缓存布局变了 ⇒ 旧断言要同步（C8）」去改**测试**，连改三轮没收敛。同一轮还用 PowerShell `Set-Content` 整文件重写两个 `.py`，把项目改成**导入即失败**（全部模块各 1 条 ERROR），只能 `git checkout --` 回退。
  - 排查：① **先看 traceback 的最后一帧** —— `File ".../sticker/sticker_manager.py", line 167, in persist_clipboard_history` 早就指名了产品文件与行号；只要最后一帧是产品代码，就不是"旧契约"而是**真 bug**。② 定位后在该函数行号区间 `grep -n "root"`，删除点与残留引用一眼可见。
  - 避免：① 迁移/重命名路径时**保留原变量**（多一个无害的局部变量，比删掉安全）；删任何变量前先在该函数区间 `grep -n "<name>"` 确认零引用。② **测试失败先分类**：traceback 最后一帧在 `tests/` ⇒ 按 C8 改断言；在产品代码 ⇒ 修产品。③ 改 `.py` **只用 edit 工具**（长锚点、3–4 行上下文；单行锚点在同文件多处重复会直接中止），**禁止** PowerShell `Set-Content`/`Out-File` 整文件重写（A3/B4）；改坏了第一动作是 `git checkout -- <file>` 回到已提交版本再重做。④ 批量正则替换后必须**逐条读**受影响用例（本轮 `mkdir()` 漏了带参数的变体，连改三轮）。

- **D22 让窗口"稍后再做事"时，接收方可能已经析构 ⇒ `RuntimeError: Signal source has been deleted`**
  - 现象：为躲开全屏遮罩，我把 `self.save_as_requested.emit(...)` 放进 `QTimer.singleShot(0, lambda: …)`；定时器触发时遮罩已 `close()`，C++ 对象销毁 ⇒ 每次多选另存为都抛该异常（`bootstrap` 记为"未捕获异常"）✗。
  - 排查：traceback 指向那行 lambda 即可确认；关键是**谁拥有这个对象、它什么时候销毁**。
  - 避免：延迟回调里**不要引用可能被销毁的 Qt 窗口/信号源**；正确分工是——**存活方同步 emit 并只捕获纯数据**（list/图像），由**应用常驻对象**（`Application`）在 `QTimer.singleShot` 里做后续 UI。写延迟回调前先问："这段跑的时候，`self` 还活着吗？"

- **D23 同步 `emit` 会阻塞到模态窗口全部结束 ⇒ 全屏置顶遮罩会挡住第 2 个及以后的对话框**
  - 现象：多选按 `A`"只弹出一个"另存为；实际 N 个都弹了，但发信号是**同步**的 ⇒ emit 返回前对话框已全部走完，遮罩这时才 `close()` ⇒ 第 2、3 个被全屏置顶遮罩压在下面。
  - 排查：看日志条数（`第 1/N` `第 2/N` …）—— 条数够但用户只看到一个 ⇒ 是**层级**问题而不是逻辑问题；反过来只有 1 条 ⇒ 才是循环/数据问题。
  - 避免：以"全屏置顶遮罩"为背景的后续 UI（对话框、菜单、提示窗）必须排在**遮罩关闭之后**（`close()` → 事件循环下一轮 → 弹窗）；同时按 D22 保证延迟回调不引用已销毁对象。

- **D24 `Popen` 不留句柄 ⇒ 子进程仍在运行时被 GC ⇒ `ResourceWarning` 刷控制台；剪贴板被占用 ⇒ Qt 内部重试刷几百行**
  - 现象：① 通知跳转用 `subprocess.Popen(["explorer.exe", …])` 后不管句柄 ⇒ `ResourceWarning: subprocess NNNN is still running`；② 别的程序占着剪贴板时 `setMimeData` 失败 ⇒ `OleSetClipboard: Failed … OpenClipboard` + `qt.qpa.mime: Retrying to obtain clipboard` 连续约 7 秒。
  - 排查：两条都属于"重试/回收发生在库内部"，业务日志里看不到原因，只能从**原始警告文本**反查调用点（`grep -n "Popen("`）。
  - 避免：① "发射后不管"的进程统一走一个助手（本项目 `app/notification_flow.py::_spawn_detached`）：保留 `Popen` 句柄、顺手清理已结束项、标准流给 `DEVNULL`；② 写剪贴板前先**探测可用性**（`editor/image_effects.py::clipboard_available`，ctypes `OpenClipboard`），占用时**跳过并只记一条 WARNING**，绝不重试（规则 14）。

- **D25 多屏下"只刷一份"与"只建一份"同罪：每块遮罩各自持有浮层，快捷键却只挂在其中一个视图**
  - 现象：遮罩内按 `X` 切换取色表达式，**不动鼠标提示条就不变**，动一下立刻变。代码里明明 `update()` + `sync()` 都调了。
  - 排查：每块遮罩各有自己的 `info_bar` / `magnifier_overlay`；`X` 的 `ApplicationShortcut` 只挂在某一个视图上 ⇒ 我刷的是那个视图的浮层，用户看的是**光标所在那块屏**的浮层。看"动鼠标就恢复"这一特征即可锁定（`mouseMoveEvent` 里会 sync 所有相关浮层）。
  - 避免：任何"改状态后要立刻可见"的刷新，都要 `for view in session.views:` 逐视图刷新（`view.update()` + 各浮层 `sync()`），并用 `getattr(..., "sync", None)` 守卫；与 D8 的"输入源只建一份"是同一类多屏缺陷，**报告里必须写明刷了哪些视图**。

- **D26 改共享绘制/组件前先核实"它到底画了什么"——否则会实现一个永远不会被画出来的参数**
  - 现象：用户要求"放大镜下面的取色提示跟随 `X` 切换"。我准备方案 B（给 `paint_magnifier` 加 `color_text=`），先读代码才发现 `screenshot/magnifier_widget.py::paint_magnifier` **只画图像/网格/准线，没有任何 `drawText`** ⇒ 那个参数会变成死代码（规则 33 禁止）。用户看到的"提示"其实是 `InfoBar`（提示条）。
  - 排查：动手前 `grep -n "drawText\|setText" <该组件>` 确认它是否真的渲染这段内容；对"某处文字不更新"类问题，先定位**是哪个部件画的**再改。
  - 避免：新增参数/分支前证明它会被消费（谁读、什么条件画）；前提不成立时按 D1/D3 **明确更正**并给出证据（文件:行号），不要为了"完成任务"塞死代码。

- **D31 同一行为有两份实现 ⇒ 修一处必然漏另一处（"左键单划 + A 弹的是老窗口"）**
  - 现象：用户报"多选另存为已是新对话框了，但**左键单划 + A** 还是老的原生窗口"。查证：`EditorWindow.save_as`（独立编辑器）已换成自绘对话框 ✓，而 `InlineEditor.save_as`（原地编辑器）**自带一份**静态 `QFileDialog.getSaveFileName` ✗ ⇒ 两套外观。
  - 排查：`grep -n "getSaveFileName\|QFileDialog(" <全库>` 数调用点；再对每个"用户可见的同一行为"问一句"它有几份实现？"（本项目常见于两个编辑器、多个选区入口、多种另存/保存路径）。
  - 避免：用户可见行为**收敛到单一实现**（本次抽 `editor/editor_window.py::build_save_dialog` 由两个编辑器共用 ✓）；改完把"同一个行为的所有入口"逐个 grep 复核并写进报告（规则 27）。

- **D32 全屏置顶遮罩下的对话框：它必须自己也置顶**
  - 现象：原地编辑按 A，另存为对话框出现在**遮罩下面**（看不见/点不到 ✗）。
  - 排查：`Qt.WindowStaysOnTopHint` 的窗口会盖住普通子对话框；多选路径因为"先关遮罩再弹窗"（D23 ✓）而没暴露 ✗，原地编辑的遮罩**不能关**（编辑器就在里面）⇒ 必然被盖。
  - 避免：以全屏置顶遮罩为背景的后续 UI，二选一——① 排在遮罩关闭之后（D23）✓；② 让对话框自身 `setWindowFlag(Qt.WindowStaysOnTopHint, True)` ✓（注意**在 resize 之前**设置，否则尺寸可能被重置）。

- **D33 "延后执行"必须覆盖整个显示周期：只推迟一轮事件循环不够**
  - 现象：为修"构造期遍历 Qt 顶层部件导致堆损坏 0xC0000374"✗，我把初始识别改成 `QTimer.singleShot(0, …)` —— 崩溃**换个栈顶又来了**：`overlay_handles ← detect_element_at_cursor`，而那一轮遮罩正处在 `show()/raise_()` 过程中 ✗。
  - 排查：`crash.log` 追加写 ⇒ 先定位**最后一块**；看到栈里有我们自己的"延迟函数"就说明**推迟得不够** ✗；对照窗口生命周期判断"这一刻部件树稳定了吗"。
  - 避免：凡"延后"都要明确**等到哪个时点**（本次：250ms + `if not self.isVisible(): 再顺延` ✓）；同一崩溃**复发**时优先怀疑"时机仍不对"，而不是换写法（规则 D15：按栈定位 ✓）。

- **D34 验证探针改写真实用户数据 ⇒ 一次崩溃就可能毁掉用户配置（本次真实发生）**
  - 现象：为验证"提示条取消勾选是否持久"，探针直接读写 `%APPDATA%\ScreenSnap\settings.json` ✗：先写入 3 个键、打算在 `finally` 里恢复；但进程在恢复前**原生崩溃** ✗ ⇒ 用户的真实配置一度被覆盖成 3 个键（所幸程序当时在运行并回写了完整配置，事后核实文件 8917 字节 ✓ 未丢）。
  - 排查：`Get-ChildItem %APPDATA%\ScreenSnap` 看 `settings.json` 的**大小与最后写入时间**（完整配置约 9KB；若变成几百字节且刚被写过 ⇒ 被覆盖 ✗）。
  - 避免：**任何验证都不得读写真实用户目录** ✗ —— 用临时目录（复制一份配置或走受支持的路径覆盖），并在探针里**先备份再动**、把恢复放在不依赖正常退出的位置（进程可能原生崩溃 ⇒ `finally` 不保证执行 ✗）；涉及用户数据的验证，报告里必须写明"用的是临时副本" ✓。

- **A11 按"行号"插入代码会因前面的插入而顶移 ⇒ 命中他处（本次连犯两次）**
  - 现象：我在同一文件里连续做"在第 N 行之后插入一行"✗，第二次的 N 已被第一次插入**顶移** ⇒ 插到了**无关代码中间**：一次导致 `IndentationError` ✗、一次把 `options.setFocusPolicy(...)` 插成了 `self.cursor_switch.setToolTip(...).setFocusPolicy(...)` ✗、还有一次插在 `button = QToolButton()` **之前** ⇒ `UnboundLocalError` ✗（用例报错，我一度以为是产品问题）。
  - 排查：`git diff -U0 <file>` 看 diff **形状**——正确应是"只有 + 行、无 - 行"；若出现 `-` 行或 + 行位置诡异，立即 `git checkout -- <file>` 回滚重做 ✓。
  - 避免：**只用文本锚点**（取 `read().lines[i].text` 的完整原文，含缩进 ✓），**绝不用行号定位** ✗；一次插入后**立即** `git diff` 核对命中位置 + `py_compile`；锚点若在文件里可能重复（如 `button = QToolButton()`），必须带上**下一行**构成唯一上下文 ✓（本次最终就是这么改对的 ✓）。

- **D27 给共享函数加防护只看了「遍历时」，没审计「谁在什么时机调用」⇒ 构造期遍历 Qt 顶层部件 = 堆损坏（0xC0000374）**
  - 现象：用户按截图瞬间程序直接消失，crash.log 只有一块 Windows fatal exception: code 0xc0000374（堆损坏），栈顶 core/window_snap.py:155 overlay_handles（list(app.topLevelWidgets())），其下依次 top_window_at → window_uia.query/element_chain → window_elements.element_chain → **screenshot/mask_window.py:1273 MaskWindow.__init__** → capture_flow.show_mask。
  - 排查：crash.log 是**追加**写的 ⇒ 先定位**最后一块**（Windows fatal exception 标记）再读栈；栈里出现 __init__ / closeEvent / deleteLater 这类构造或析构帧 ⇒ 说明查询发生在部件树**变动期间**；shiboken6.isValid 挡不住这种崩溃（它保护我们自己的调用，挡不住 Qt 内部遍历）。
  - 避免：① **绝不在窗口构造/析构期间枚举 Qt 顶层部件**（topLevelWidgets / allWidgets / winId）——需要就先 QTimer.singleShot(0, …) 延后到事件循环空闲（规则 18 的同一条道理），本次即把初始识别搬到 detect_element_at_cursor()；② 延迟回调里补 try/except RuntimeError（遮罩可能已被关掉）；③ 改共享函数时**连调用时机一起审计**（规则 27），别只加固函数体。

- **D28 加了「重入守卫」标志位却忘了复位 ⇒ 第一次调用之后功能整体失效**
  - 现象：为 D27 加 _ENUMERATING 守卫时写成「置 True 后直接 return」，**没有 finally 复位** ⇒ 第二次起每次枚举都返回空集合 ⇒ 窗口识别整体失效（比崩溃更隐蔽：不报错、只是识别不到东西）。
  - 排查：改完立刻写 5 行探针连续调用两次同一函数，并**断言标志位已复位**（assert window_snap._ENUMERATING is False）——本次探针打印「第一次=0 个句柄，第二次=0 个句柄，_ENUMERATING=False」才确认没坏。
  - 避免：任何「标志位 + 提前 return」的守卫，一律 try/finally 复位，并在同一轮里用探针**实测复位**（只看代码「看起来会复位」不算；规则 33 的「接线完整吗」同样适用于状态位）。

- **D29 把可 patch 的静态 API 换成「构造实例 + exec()」⇒ 测试拦不住 ⇒ 真实模态对话框把用例挂死（C1 复发）**
  - 现象：为实现「另存为对话框居中」，把 QFileDialog.getSaveFileName(...) 改成 QFileDialog(...) 加 dialog.exec()；test_capture 全量从此**永不返回**（无摘要、无输出），而单跑 -k quick / -k mask 却是绿的。
  - 排查：grep -n 找 getSaveFileName 在 tests/ 里的用法 ⇒ 用例 patch 的是**静态方法**（patch(editor.editor_window.QFileDialog.getSaveFileName, return_value=(path, 空串))）⇒ 换成实例后补丁失效、弹出真实模态框；症状是「某模块整体挂住 + 子集全绿」，与 C1 同型。
  - 避免：凡是**测试需要拦截**的外部交互（文件对话框、消息框、系统 API），保持**静态/类方法入口**便于 patch；需要非原生行为时用**同一个静态入口的参数**（如 options=QFileDialog.Option.DontUseNativeDialog）而不是换成实例；确需实例时先把测试改成 patch 工厂函数并在同一轮更新。

- **D30 往类体里插模块级代码（列 0 语句）⇒ 类提前结束，后面的方法全部变成嵌套函数**
  - 现象：给 editor/editor_window.py 加「汉化助手」时把 _CHINESE_DIALOGS_READY = [False] 与 def _ensure_chinese_dialogs() 写在**类体中间**（列 0）⇒ 类在该行结束，之后所有 4 空格缩进的 def 都变成该函数的**嵌套函数** ⇒ AttributeError: EditorWindow object has no attribute invalidate_rotation_reset（9 条 ERROR），dir(EditorWindow) 只剩 showEvent。**过程无语法错**（缩进合法）⇒ 比 IndentationError 更难查。
  - 排查：py_compile 通过却报 AttributeError 时，打印 dir(类)（缺方法即定位）；再用列首正则列出**列 0 行**，看类体内是否混进了模块级语句。
  - 避免：模块级常量/函数一律放在**类之前**（或文件末尾）；往类里插入代码前先确认插入点的缩进层级；改完立刻 py_compile **并**跑一次受影响模块的用例（编译通过不等于类结构完好）。

- **D39 剪贴板历史内部回写触发外部变化链，轮播被自己重置**
  - 现象：给 Ctrl+F3 贴出的卡片补图片复制后，内部写入触发 `dataChanged`；若照外部复制入史，会把历史重排并将轮播索引重置，后续一直回到首项。
  - 排查：追踪 `publish_output → dataChanged → _clipboard_changed/_remember_clipboard_source`，分别检查同步再入和守卫撤销后到达的延迟通知；连续轮播各来源并对比历史键与索引，另输入真正的外部新内容核对仍可入史。
  - 避免：只在内部历史发布边界设置同步守卫，用 `try/finally` 复位；以发布内容键识别延迟自通知，外部内容变化时清除该标记。原始文字/HTML/文件信息保留在 origin/历史，不为方便去重扩展自动复制格式；测试同步、延迟、外部变化、复制关闭/失败及连续轮播。
- **D40 Qt 图像存储格式差异不能直接当成输出内容变化**
  - 现象：2026-10-14 成品一致性回归中，QPixmap 转回的 `Format_ARGB32_Premultiplied` 与输出 QImage 的 `Format_RGBA8888` 被直接比较，出现格式/alpha 存储差异，混入真正的漏复制与重复效果失败。
  - 排查：分别打印格式、尺寸、devicePixelRatio、行跨度与 alpha 处理方式，再比较规范化后的像素；不能只看对象 format 或原始字节就下结论。
  - 避免：内容一致性断言先转换为明确的共同格式（本轮用预乘 ARGB32）并核对尺寸/像素；若测试的是编码或存储格式，就另设断言，不以归一化掩盖该契约。逐像素/字节比较须处理行填充与透明像素，不用扩大容差让真实内容差异变绿。

- **D41 混合 DPI 跨屏抓取不能用来源窗口局部点重建全局点**
  - 现象：2026-10-14 用户报告上大下小排布拖框进入下屏后回跳，光标已到底部而高亮仍留在上屏；上屏4K@125%、物理原点 `(-751,-2160)`，下屏1920×1200@100%，Qt 原生布局存在纵向间隙。独立全局字段的合成轨迹复现约270物理像素后的映射反跳；不能把这项证据写成真实桌面投递模型已验收。
  - 排查：对照日志中的物理/Qt 原生屏幕矩形，比较 `event.globalPosition()` 与 `mapToGlobal(event.position())` 重建值；追踪 `MaskWindow._mouse_event_position`、`InlineEditor.eventFilter` 及徽标轮询对共享端点的写入。`tests.test_capture_pointer` 的 `test_reported_vertical_gap_does_not_jump_back_after_270_pixels` 和连续336种抓取组合使用独立全局/局部字段复现旧错误，避免测试夹具把错误假设复制进预期值。
  - 避免：鼠标抓取的 press/move/release 直接转换事件的 Qt 原生 `globalPosition()`，转发时保留该字段，浮点保留至物理转换；禁止按来源屏幕 DPR 重建跨屏端点，也不能用当前 `QCursor.pos()` 代替事件落点。徽标等轮询只读取实时光标，不抢写拖动/键盘接管中的交互端点。修改前后覆盖主副屏双向、上下错位/负原点/混合DPI/布局间隙、两种局部事件模型、释放补末段/吸附/取色/快速贴图；合成回归与真机验收分开报告，真机还要验证原地已有选区跨屏调整后的编辑层重建。

## Validation

- Use the project environment's `python` executable. On the expected Windows setup, `py -3` may resolve to a different interpreter without project dependencies.
- Every test, probe and import check must follow rule 36: explicit absolute interpreter/cwd, enforced per-process and batch deadlines, visible start/end/elapsed time and periodic progress, and bounded process-tree cleanup on timeout. A runner without child timeouts (including the current `run_tests.py`) must be wrapped by an external per-module executor; do not run it unbounded or treat background polling as a timeout.
- The suite is split by feature under `tests/` (shared base: `tests/base.py`): `test_config`, `test_capture`, `test_sticker`, `test_uia`, `test_dpi`, `test_ui`, `test_app`, `test_misc`, plus `tests/editor/` (`test_canvas`, `test_tools`, `test_text`, `test_erase`, `test_zoom`). Run the narrowest relevant module first (e.g. `python -m unittest tests.test_dpi`) and always in its own process: a module that leaves mask/editor/focus state behind fails unrelated later cases, and one shared process also hits the offscreen shutdown crash. There is no `tests.test_core` any more.
- Focused regressions also live in `tests.test_capture_pointer`, `tests.test_sticker_lifecycle`, and `tests.test_sticker_mask`; include them explicitly rather than assuming `run_tests.py` lists every module. For sticker clipping, run `tests.test_sticker_mask` in fresh processes with `QT_SCALE_FACTOR=1/1.25/1.5/1.75` as well as its mocked-DPR matrix; an instance method mock is not evidence about the actual Qt platform DPR or the Windows native window region.
- Each test module runs standalone (`python tests/test_app.py`), via `python -m unittest tests.test_app`, or under `discover` from either the repository root or the `tests/` directory; modules bootstrap the repository root onto `sys.path`, so never rely on the caller's cwd.
- Qt widget tests run offscreen in CI and may emit platform clipboard or window warnings. Report test counts and failures from unittest's final summary, not from those warnings.
- Real global hotkeys, mixed-DPI display geometry, UI Automation providers, and click-through behavior require Windows desktop verification when a change affects them.
- The suite is 500+ Qt tests; a single-process run can stall or crash natively at offscreen shutdown. Prefer chunked runs (e.g. 20 tests per subprocess with the enforced deadline in rule 36). A positive `Ran N / OK` with a known native shutdown exception is reported as summary passed with abnormal shutdown, not normal exit; no summary, zero collected tests, timeout or an unexplained non-zero exit is not a pass. Tests that genuinely need a real desktop screen follow rule 19; never remove coverage or add a skip to hide a regression.

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

- **D37 画布绝不"吸附/移动"鼠标指针 —— 只有显式的键盘微调跟随例外**
  - 现象：历史上有一版改动让画布在拖动/绘制时把指针"吸"住 ✗（用户明确要求修掉 ✓ 现已修复 ✓），用户特别提醒"要注意这一点" ✓；本会话我又新加了"键盘微调时指针跟随"✗ ⇒ 两者很容易被混为一谈 ✗。
  - 排查：全库列出会移动指针的地方：`grep -n "QCursor.setPos"` + 打印每个调用点的所属函数 ✓（本次实测只剩 5 处 ✓：`_follow_handle_cursor` / `_follow_move_cursor` / `MaskWindow.__init__`（遮罩打开时摆初始位置 ✓）/ `_nudge_active_region` / 遮罩 `keyPressEvent` 两处 ✓）⇒ 逐条判定它属于"键盘动作"还是"鼠标拖动/绘制" ✓。
  - 避免：① 指针只能在**用户按了键盘微调键**（WASD/方向键）时被程序移动 ✓，并且必须**同步跟随选区/标注/手柄**✓；② 任何鼠标拖动、绘制、悬停、绘制期路径都**不得** `QCursor.setPos` ✗；③ `_follow_move_cursor` 还要求"左键确实按着" ✓ 否则一律不动指针 ✓；④ 跟随统一走 `_apply_cursor_follow()` ✓（立刻设 + 事件循环下一轮读回 `QCursor.pos()` 差 > 2px 再设一次 ✓ 解决"不及时/不同步" ✓）；⑤ 改输入路径后按上面那条 grep 复核一次 ✓。

- **D38 被覆盖的子控件判定不能依赖「父窗口 paintEvent 会被 update() 触发」—— 左键整屏徽标不隐藏连修三轮才定位到**
  - 现象：左键划满整屏进入原地编辑后，尺寸徽标移到鼠标上**不隐藏**；右键多选（不进原地编辑）却正常 ✓。反复修了三轮（容差 2→14→40→100、周期重绘、实时光标）都没彻底解决 ✓。
  - 排查：三环断点逐环定位（靠加可观测点，不靠猜）—— ① 徽标周期刷新的 `QTimer` **从未启动** ✗：启动逻辑放在 `InfoBar.sync()` 里误用 `self`（InfoBar），`self.selection` 恒为 `None` ⇒ `_rects_now` 恒空 ⇒ timer 从不 `start()` ✓（改 `self`→`view` 即 MaskWindow ✓）；② 进入原地编辑后画布（遮罩子控件）拦截 `mouseMoveEvent` ⇒ `self.position` 停在松开处，hover 判定用过时坐标 ✓（改每拍 `QCursor.pos()` 刷新 ✓）；③ **最终根因** ✗：即使 timer 启动、position 也刷新了（日志 `周期刷新第 21 拍: position=(1233,27)` 已在徽标上），`_badge_poll_tick` 里的 `self.update()` 在遮罩被画布**完全覆盖**后**不触发 paintEvent**（Qt 跳过被遮挡窗口的重绘）⇒ 依赖 `paintEvent → _sync_size_badge` 的判定链路断掉，徽标判定始终没跑 ✓（改为 tick 里**直接**算 `element_rect/size` 并调 `_sync_size_badge` ✓）。
  - 避免：① 覆盖层/多屏下，子控件的可见性/几何/判定**不得**依赖「父窗口 `paintEvent` 会被 `update()` 触发」✗ —— 被另一个（子）控件完全遮挡时 Qt 会跳过重绘 ⇒ 周期任务里要**直接**计算目标状态并调用同步函数（可见性仍走延迟回调，D35 ✓），而不是 `self.update()` 间接触发 ✗；② 定时器/共享状态挂载时先确认 `self` 归属（InfoBar vs MaskWindow）—— 跨类引用不存在的属性会静默返回 `None`，逻辑空转还不报错 ✗；③ 这种「多环断点」问题**先加可观测点逐环定位**（timer 是否启动 / 周期 tick 是否刷新 position / 判定是否出「决策」日志），而不是反复猜测修同一处 ✓。
