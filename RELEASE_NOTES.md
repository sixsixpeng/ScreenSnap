# ScreenSnap 发布记录

> 本文件仅用于打包与发布准备，按 AGENTS.md 保持未提交；本轮为 2026-10-14，验证限制与最终结果见 README 的调整记录。

## 1. 贴图输出与剪贴板

- **1.1 修复** `app/sticker_flow.py::add_quick_sticker` 原来显式关闭剪贴板复制，左右键框选、按住 Space 框选及多选快速贴图虽保存文件但不能直接粘贴图片；现在按 `copy_saved_image` / `copy_saved_path` 发布本次输出，两项皆关仍保留原剪贴板。验证：`test_clipboard_output_left_right_space_drag_gestures`、`test_clipboard_output_multiselect_preserves_order_and_last_image`、`test_clipboard_output_quick_sticker_has_one_effect_pass`。
- **1.2 修复** 两个编辑器的贴图按钮/Space 原来 `save(automatic=True)` 未启用复制，并将贴图与保存分别合成；现在 `EditorWindow.execute` / `InlineEditor.execute` 同一成品只合成一次，保存后按开关复制再贴出，保存失败仍贴图但只允许复制图片，不发布上一张路径。`save_capture_images(final_output=True)` 保存已经装饰的快速贴图，避免重复圆角/边框/阴影；通知显式使用本次保存路径。验证：`test_clipboard_output_editor_paste_buttons_and_keys`、`test_clipboard_output_paste_failure_still_copies_image_without_stale_path`。
- **1.3 修复** 原地编辑的全局快速贴图入口原来裁冻结截图，可能丢失已有标注；现在委派当前编辑器，和按钮/画布 Space 共用会话消费守卫。左右键按住快速贴图键开始框选均可在松手后输出，原地调整选区时的 pending 在松手后消费，副视图松键不再解引用空快捷键。验证：`test_clipboard_output_editor_paste_buttons_and_keys`、`test_clipboard_output_inline_resize_consumes_pending_space`、`test_clipboard_output_secondary_release_uses_configured_key`。
- **1.4 修复** `StickerManager` 的 F3、历史切换、文件新建/替换、Ctrl+F3 和管理面板添加原来只显示图片、不按复制设置输出；现在在各主动命令边界调用 `publish_output`。剪贴板卡片保留原始信息在 origin，自动输出仅图片，不附加 HTML/文件对象格式；同步守卫与发布内容键防止自身回写重排历史。图片历史排除内部 cache，底层 add、会话/回收站恢复及缓存持久化仍不复制。验证：`test_clipboard_output_file_history_and_panel_commands`、`test_clipboard_output_clipboard_cycle_keeps_history_and_origin`、`test_clipboard_output_internal_cache_restore_and_recycle_are_silent`、`test_clipboard_output_file_history_excludes_private_image_caches`。
- **1.5 修复** `StickerManager.clear_clipboard_history → paste_clipboard` 原来在内部发布图片后清空历史，保留的自发布键会拒绝当前有效图片；现在仅主动贴出且历史为空时接纳当前实际剪贴板并安排持久化，仍用发布键拦截延迟自通知，不自动回填已清历史、不恢复已清原文。验证：`test_clear_history_ignores_self_notifications_until_explicit_paste`（五种来源/四复制组合/两个清空入口）、外部新内容与空剪贴板回归，`python -m unittest tests.test_sticker_lifecycle` 14条正常通过。

## 2. 保存、显式复制与失败边界

- **2.1 修复** 原地编辑普通保存、遮罩 Y/确认后仅复制、两编辑器仅复制和贴图右键复制图像原来存在独立写入实现，剪贴板被占用时可能仍关闭并提示成功；现在共用 `editor/image_effects.py` 的预检及异常处理，显式仅复制检测失败时保留编辑/遮罩。已保存文件不会因自动复制跳过而回滚；业务层不重试，不声称消除了预检与 Qt 写入间的系统竞争。验证：`test_clipboard_output_busy_board_keeps_only_copy_editor_open`、`test_clipboard_output_mask_copy_and_confirm_copy_keep_session_on_failure`、`test_clipboard_output_busy_board_and_writer_errors_do_not_break_saved_paste`。
- **2.2 修复** `InlineEditor.save` 成功复制后裸调用 `os.startfile`，打开目录异常曾会被误当保存失败、再次复制且丢掉有效路径；现在局部记录警告并保留保存成功结果。原地切多选时选择保存编辑也按开关复制；显式复制路径未保存时先静默保存，不再复制 None，占用时不报成功。验证：`test_clipboard_output_inline_directory_error_does_not_copy_twice`、`test_clipboard_output_switch_to_multi_save_obeys_copy_options`、`test_clipboard_output_explicit_path_saves_once_and_does_not_copy_image`。
- **2.3 调整** 设置页分组由“手动保存后复制”改为“输出后复制”，说明所有主动保存/贴图入口及图片/路径独立规则；README 增加场景矩阵和两键默认值/类型说明。未新增 schema、修改默认值或版本保留清单；截图可配置保存键与独立编辑器 S/Ctrl+S 仍按原作用域区分。验证：既有 `test_save_clipboard_options_persist_independently`、`test_save_clipboard_options_apply_to_both_editors`，新增 `test_clipboard_output_live_copy_preferences_apply_without_reopening_editor`，以及配置全入口回归。

- **2.4 调整** README 补充 `copy_saved_to_clipboard` 的尽力写入边界：预检和异常隔离不等于复制成功回执，自动失败仅警告、不重试，显式仅复制检测失败保留界面；本轮未更改写入实现或新增重试/失败通知。验证：核对 `QClipboard.setMimeData` void 调用及两个编辑器边界，`ClipboardOutputTests` 22条正常通过。

## 3. 贴图显示、会话、回收站与替换

- **3.1 修复** `StickerManager.persist → cleanup_cache` 原来只保留活动会话源图，关闭进入回收站后防抖保存会删除其私有文件，恢复后会话指向失效源图；现在所有清理入口合并传入来源、活动（含隐藏）窗口与回收站引用，删除/清空/超限后只回收最后引用已移除的私有缓存。验证：`test_persist_preserves_recycled_source_and_restore_remains_readable`、`test_cleanup_cache_reclaims_sources_after_recycle_removal`、共用源图与无会话恢复引用回归；既有 `test_sticker -k cache/recycle` 前后摘要均通过。
- **3.2 修复** `StickerManager.persist` 原来跳过已析构窗口状态后，日志和布局又访问全部窗口，部分坏窗口仍会阻断有效状态落盘；现在日志与布局复用一次读取的成功快照，布局异常只记警告，正常会话仍按临时文件原子替换，全坏保留旧会话、空列表仍写空会话、写入失败不清源图。验证：`test_persist_mixed_bad_and_good_windows_saves_single_snapshots`、全坏/布局失败/原子替换失败/空会话回归，既有 `test_sticker -k persist` 6条正常通过。
- **3.3 修复** `StickerItem.replace_image` 的文件替换/历史轮播原来同尺寸不触发 resize 而继续使用旧透明遮罩、未及时持久化新 source，新 Pixmap 也可能没有当前 DPR；现在显式重设 DPR、刷新输入遮罩并通知保存，保留位置和倍率，旋转仍归零。验证：`test_same_size_replace_refreshes_mask_preserving_geometry_and_dpr` 128种组合及无 alpha 图、两个实际换图入口回归；既有替换/圆角遮罩2条前后有 OK 摘要但收尾 `3221226505`，不能视为正常退出，真实主副屏命中仍未验收。
- **3.4 修复** `StickerItem.update_input_mask` 的 alpha 遮罩已按逻辑尺寸缩放，却保留源 Pixmap 的屏幕 DPR，点式 `drawImage` 再缩一次，将右上/右下/左下透明圆角挪到图片内部；Space、独立/原地编辑器按钮与托盘文件入口共享此问题，保存图片不受影响。现在临时逻辑遮罩 DPR 归一为1，保留真实圆角/镂空和源图，不靠禁用透明遮罩掩盖缺口。验证：`test_actual_qt_dpr_rounded_mask_has_no_internal_cutouts` 在125%修改前明确失败；`python -m unittest tests.test_sticker_mask` 8条在实际 Qt 离屏100/125/150/175%四档均正常通过，含768种模拟DPR/倍率/图像/背景/描边/阴影组合和四输出入口源图/保存像素一致性检查；真实 Windows 原生裁剪与鼠标命中仍未验收。
- **3.5 修复** `StickerItem.apply_style/rotate/reset_rotation/reset_size` 原来依赖 resize 事件，同尺寸样式切换、方图旋转及隐藏窗口恢复倍率可沿用旧遮罩；现在显式同步，滚轮共用样式路径，并在 `DevicePixelRatioChange` 通知中更新图像比例和逻辑遮罩，构造期状态未就绪时不处理。诊断仅记录遮罩状态变化，不逐绘制/轮询重复输出；未新增配置或改变快捷键。验证：同尺寸样式、DPR事件、方图旋转、0.1–10倍率重置、30次不变状态诊断回归；原六组45条次前后正常通过，滚轮/批量/恢复/会话/回收扩展18条次正常通过。混合DPI主副屏拖动、热插拔及运行中改缩放仍未做真机验收。

## 4. 截图跨屏鼠标坐标

- **4.1 修复** `MaskWindow` 鼠标按下/移动/释放及 `InlineEditor.eventFilter` 原来用来源窗口局部坐标重建全局点；上方4K@125%、下方1920x1200@100%、负原点横向错位布局的合成抓取事件会落入原生间隙并将选区反跳到上屏。现在 `_mouse_event_position` 直接换算事件 `globalPosition()`，原地手柄与 Alt/Ctrl 取色共用该来源，保持含端点吸附和整桌面夹取。验证：`test_reported_vertical_gap_does_not_jump_back_after_270_pixels`、连续336种组合、快速贴图裁图/锚点回归，新增模块9条正常退出；真实桌面投递模型与同布局复测仍未验收。
- **4.2 修复** `_badge_poll_tick` 原来在拖动/键盘接管期间覆盖交互端点；现在只在非交互时同步 position，徽标仍独立读取实时光标。实时光标与 C 取色不再绕回窗口局部坐标；不一致输入诊断固定签名限频，30次重复仅一条日志。验证：轮询交错、实时光标/取色、键盘释放和日志节流回归；原有截图115、DPI54、mask22、hover1、UIA58前后有 OK 摘要，除hover外仍有 `3221225477` 收尾，不能算正常退出；不改变截图手势、状态或提示条文案。

## 5. 截图尺寸提示条与标签隐藏距离

- **5.1 调整** `screenshot/hint_items.py::pixel_size_text` 原来返回 `宽 x 高 物理px`，截图尺寸提示条与尺寸标签（子控件 QLabel）都带“物理px”后缀；现在只返回 `宽 x 高`（尺寸仍是截图物理像素，语义不变，仅去掉单位）。同步更新 `tests/test_capture.py::test_capture_size_text_uses_physical_pixels_in_all_selection_states` 与 `tests/test_uia.py` 的文案断言；`ui/sticker_panel.py` 自带的 `物理px` 属于贴图管理面板/回收站，不属截图尺寸提示条，未改动。验证：`python run_tests.py test_capture test_uia`（本轮未执行）。
- **5.2 新增** 尺寸标签“光标靠近即隐藏”的容差原来在 `MaskWindow._sync_size_badge` 写死 100px；现在改为配置键 `window_hover_badge_hide_margin`（范围 0–400，**默认按用户要求取 10**），光标进入标签矩形四周该距离内即隐藏、移开立即恢复，0 表示只在标签本身上隐藏，命中区仍限制在选区内部。设置页“截图 > 窗口与控件识别”新增“尺寸标签隐藏距离(px)”；`DEFAULTS`/`validate()` 越界拒绝/`repair()` 回默认/页与全部重置/导入导出随既有生命周期生效，属外观参数不加入 `PRESERVED_ON_VERSION_RESET`；README 配置表补行。验证：`python run_tests.py test_config`（`test_badge_hide_margin_default_and_bounds`）、`python run_tests.py test_capture`（`test_badge_hide_margin_is_read_from_config`）（本轮未执行）。

## 6. 全局热键被抢占后的判定与恢复

- **6.1 修复** 热键被其它程序抢占后“一直不恢复、也没有日志”：`HotkeyManager` 的旧判据只有「系统近期有输入而自家通用钩子没收到」(`_hook_missed_input`)，而抢占方通常只吞掉某一个组合键 —— 敲别的键/动鼠标仍刷新 `_observed_input` ⇒ 判据恒为“健康” ⇒ 从不重装、也不打日志。现在新增按热键判据 `_intercepted_hotkey`：通用钩子在键盘链上看到某热键**主键按下且修饰键恰好等于绑定**（多按一个修饰键时 keyboard 不会触发该热键），回调却**连续 `REQUIRED_MISSES`(2) 次**都没触发 ⇒ 判定被抢占/吞键，按 `RETRY_MIN_SECONDS`(30s) 限速重装并记 WARNING（含动作名与次数）；`emit_action` 触发过即清零计数。
  ⚠ **回退记录（本轮真机把热键整体改坏，如实记录）**：第一版在通用钩子回调里调用 `keyboard.is_pressed` 判断修饰键、且只要求“修饰键是绑定的超集”，并在重装时先卸再挂观察钩子 —— 真机上 18 个热键表现为「仍显示已注册、通用钩子也能看到按键，但热键回调再也不触发」，日志里还留下一次 `repeat` 误报重装（`check_health` WARNING）。现已**彻底移除钩子回调中的一切 keyboard API 调用与卸钩/重挂**，改为纯 Python 维护按下的修饰键集合、精确匹配、连续两次判定，并新增结构性护栏用例断言 `_note_input` 源码里不出现 keyboard 调用。验证：`test_misc.test_hotkey_intercepted_criterion_triggers_reinstall`（`.venv` + offscreen，与既有 3 条热键用例一起 `Ran 4 tests ... OK`）。
- **6.2 修复** 恢复链路可能“静默死掉”且会“复活已关闭热键”：`listen()` 原只捕 `ValueError/OSError`，而 `install()`/`clear()` 里的 `keyboard.remove_hotkey` 抛其它异常会让**监听线程直接退出**（此后注册/重装请求石沉大海且无日志）——现在 `listen()` 兜底所有异常并记 ERROR 继续运行、`clear()` 逐句柄兜住；`hotkeys_enabled` 关闭时清空 `_last_request`，避免后续体检把上一次启用时的绑定重新注册回来。验证：`test_misc.test_hotkey_worker_survives_unexpected_errors`。
- **6.3 新增** 手动恢复入口与暂停留痕：托盘菜单“重新注册全局热键”与设置页“快捷键 > 全局热键 > 重新注册全局热键”按钮，除按当前设置重注册全部热键外，还会**清除“设置页正在录制”留下的暂停标记**（该标记卡住时热键表现为“注册着但按了没反应”）；`pause_hotkeys` 增记一条 INFO（已暂停/已恢复），便于从日志区分“没注册”和“被暂停”；设置页体检说明补上“连续两次未触发回调”。验证：`python run_tests.py test_misc test_config test_app`，另用 18 个默认热键 + `suppress` 的真实规模复现脚本确认注册、设置刷新、被判抢占后重装都能完成且**重装后仍能触发**（本轮未跑全量测试）。真机未验收：第三方程序实际抢占、系统静默摘钩、`suppress` 拦截链路，以及“按键确实被吞时判据是否按预期重装”。

## 7. 十字线样式三选一与新参数

- **7.1 调整** 设置页「截图 > 定位辅助」原来用「十字线自适应颜色」复选 + 「自适应方式」下拉两个控件才表达三种状态（关掉开关后“方式”仍显示、可改却无效果）；现在改为**一组互斥单选按钮**（单色 / 双色描边 / 逐像素反色），直接对应生效样式。为此 `ui/widgets/tooltip.py` 新增 `RadioRow` 单选行与 `SettingsPage.radios()`（支持联动回调）。验证：`tests/test_config.py::test_crosshair_modes_radios_rows_and_rendering`（本轮未执行）。
- **7.2 新增** 按样式隐藏无关参数行：`ScreenshotPage._sync_crosshair_rows()` 用 `QFormLayout.setRowVisible` 只显示当前样式真正生效的行（单色→十字线颜色；双色描边→亮度分界 + 暗底/亮底主线色 + 描边加宽量；逐像素反色→反色强度 + 交叉点间隙 + 中灰补偿），线宽三种样式共用故始终显示；切换样式、恢复本页默认与导入配置后都会重算。验证：同上用例的行可见性断言（`isHidden()` 逐项）（本轮未执行）。
- **7.3 调整** 配置键重构（schema 变更，按规则 30 走完整生命周期）：删除 `crosshair_adaptive` / `crosshair_adaptive_mode`，新增 `crosshair_mode`（`solid`/`contrast`/`invert`，默认 `contrast` 等价旧默认）与 7 个参数（`crosshair_contrast_threshold`、`crosshair_contrast_dark_bg_color`、`crosshair_contrast_light_bg_color`、`crosshair_contrast_halo_width`、`crosshair_invert_strength`、`crosshair_invert_gap`、`crosshair_invert_halo_enabled`）。`migrate_legacy_settings` 在加载与导入配置时一次性把旧两键推导为新键（关=单色；开 + `invert`=逐像素反色；其余=双色描边）随后丢弃旧键；`DEFAULTS`/`validate()` 边界/`repair()`/页与全部重置/导入导出/README 配置表同步，属外观项不加入 `PRESERVED_ON_VERSION_RESET`；本轮不升 `APP_VERSION`（不触发版本闸门清空用户数据，旧配置靠迁移平滑过渡）。验证：迁移与越界断言（本轮未执行）。
- **7.4 调整** 遮罩与设置页预览都改为按 `crosshair_mode` 三分支：双色描边支持自定义双色、亮度阈值与描边加宽量（默认黑白对 + 阈值 128 = 旧行为）；逐像素反色支持反色强度（按不透明度混合）、交叉点间隙（0 = 不断开）与中灰补偿描边（采样亮度落在阈值 ± `CROSSHAIR_MID_GREY_BAND` 内时补一条细描边）。验证：`test_config` 两条十字线用例（黑白底渲染、反色交叉点只反一次与断口外侧）（本轮未执行）。

## 8. 设置页小枚举改单选行与「更多设置」重叠修复

- **8.1 调整** 设置页里取值 ≤10 个的枚举项由下拉框改为**互斥单选按钮组**（`RadioRow` 增加折行与 `value()`/`set_value()`/`changed`），共 31 处；≤4 项排一行、5–10 项自动折两行；>10 项的（默认标注工具、箭头样式、序号形状）与动态字体列表仍保留下拉。联动代码同步改用 `set_value`：`window_hover_fill_mode`（置灰填充项）、`sticker_background_mode`（预览）、`sequence_preset`（套用预设）、`settings_window.set_annotation_setting` 的逐键同步（并把漏列的下拉键 `sequence_shape` 补上）。验证：`test_config.test_small_choice_controls_use_radio_row`（4 项一行点击落盘 + 恢复默认、8 项折两行、>10 项仍为下拉），以及 `test_dpi`/`test_uia`/`test_config` 中 4 处旧断言改写（本轮未执行）。
- **8.2 修复** 「更多设置」弹层控件重叠（用户报告文字标注底部两项叠在一起、显示橡皮擦的 tooltip）：橡皮擦「涂抹模式（自由笔刷）」被放在文字工具「文字高度」所在的格（行 36 第 0 列）⇒ 同一格两个控件重叠；橡皮擦行集又仍写着行 33（那是「椭圆填充色」）⇒ 该弹层显示别的工具的填充色、看不到自由笔刷。现在自由笔刷独占唯一空闲行 14，橡皮擦行集改为 (1, 14, 31)。验证：`test_config.test_option_panel_rows_do_not_cross_tools`（含"选项面板不得有同格控件"的结构性护栏）（本轮未执行）。

