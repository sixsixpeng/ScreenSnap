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
11. Keep expensive first-capture file encoding/writes off the input-event path when behavior permits. Show the editor before scheduling its initial save, retain the current save/error behavior, and avoid duplicate capture/save notifications for one capture.
12. Prefer shared editor primitives with explicit context adapters over duplicating the annotation canvas or merging the two window lifecycles. `InlineEditor` and standalone `EditorWindow` have different ownership, sizing, capture, and close semantics; consider both before changing shared state.
13. Every new feature or behavior change must add logs at meaningful control and failure boundaries. Use `DEBUG`/`TRACE` for diagnostic details, `INFO` for user-visible lifecycle milestones, `WARNING` for recoverable unexpected conditions, and `ERROR` for failed operations that need attention. Include actionable context without logging image pixels, clipboard contents, or sensitive user data.
14. Keep logs quiet on high-frequency paths: never log each timer tick, pointer move, paint, polling iteration, or unchanged state. Log transitions, throttled summaries, or exceptional exits instead; test repeated interactions to ensure they do not flood the log.
15. Update this guide when the project structure, required workflow, test commands, or documentation policy changes.

## Validation

- Use the project environment's `python` executable. On the expected Windows setup, `py -3` may resolve to a different interpreter without project dependencies.
- Run the narrowest relevant unittest first, then run the full suite with `python -m unittest tests.test_core`.
- Qt widget tests run offscreen in CI and may emit platform clipboard or window warnings. Report test counts and failures from unittest's final summary, not from those warnings.
- Real global hotkeys, mixed-DPI display geometry, UI Automation providers, and click-through behavior require Windows desktop verification when a change affects them.

## Ownership Map

- `screenshot/mask_window.py`: multi-monitor screenshot surface, selection rendering, selection actions, inline editor lifecycle.
- `screenshot/selection_rect.py`: physical-pixel selection geometry and resize behavior.
- `editor/annotation_canvas.py`: annotation scene, view-only overlays, editing interactions, undo history, and unadorned scene rendering.
- `editor/editor_window.py`: standalone editor behavior and consistent save/copy output.
- `editor/image_effects.py`: final output transformations shared by saving, clipboard, and output preview.
- `config/config_manager.py` and `ui/settings_editor.py`: persistent configuration defaults, validation, and settings controls.
- `tests/test_core.py`: focused UI, configuration, rendering, and interaction regression coverage.
- `README.md`: user instructions, developer notes, and chronological adjustment log.
