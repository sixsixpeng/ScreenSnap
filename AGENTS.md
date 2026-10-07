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

21. **Never commit on your own initiative.** Finish the work, verify it, then tell the user it is ready and suggest the commit message(s); run `git add`/`git commit` only after they explicitly ask. Reminding them that uncommitted work exists is expected and welcome.

## Validation

- Use the project environment's `python` executable. On the expected Windows setup, `py -3` may resolve to a different interpreter without project dependencies.
- Run the narrowest relevant unittest first, then run the full suite with `python -m unittest tests.test_core`.
- Qt widget tests run offscreen in CI and may emit platform clipboard or window warnings. Report test counts and failures from unittest's final summary, not from those warnings.
- Real global hotkeys, mixed-DPI display geometry, UI Automation providers, and click-through behavior require Windows desktop verification when a change affects them.
- The suite is 500+ Qt tests; a single-process run can stall or crash natively at offscreen shutdown. Prefer chunked runs (e.g. 20 tests per subprocess with a timeout) and read a non-zero exit together with its `OK` summary as the known shutdown crash, not a failure. Tests that genuinely need a real desktop screen (window handles, a screen wider than the editor) should be `unittest.skip` placeholders instead of failing.

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
- `tests/test_core.py`: focused UI, configuration, rendering, and interaction regression coverage.
- `README.md`: user instructions, developer notes, and chronological adjustment log.
