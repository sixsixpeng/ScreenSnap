# ScreenSnap

ScreenSnap 是一个基于 Python、PySide6 的 Windows 优先截图与贴图工具。程序启动后常驻系统托盘，没有常驻主窗口。支持混合 DPI 多显示器截图、单选区原地编辑、多区域截图、矢量标注、图像变换、PNG 保存、剪贴板导入导出和独立置顶贴图。

本文同时作为**用户操作手册**和**开发维护指南**。界面、快捷键、配置项或工作流发生变化时，应在同一改动中更新相应章节和测试说明。

## 目录

- [快速开始](#快速开始)
- [操作手册](#操作手册)
- [默认快捷键](#默认快捷键)
- [设置和数据](#设置和数据)
- [技术指南](#技术指南)
- [测试与打包](#测试与打包)
- [已知限制](#已知限制)

## 快速开始

需要 Windows 和 Python 3.10 或更高版本（安装时勾选加入 PATH）。在项目目录运行
[`start up.bat`](start%20up.bat)：脚本检查系统 Python；若缺失则中文提示并等待按键退出。
若 `.venv\Scripts\pythonw.exe` 缺失，脚本会删除旧 `.venv` 并重建；随后激活虚拟环境、
安装 [requirements.txt](requirements.txt)，最后以 `pythonw main.py` 启动托盘程序。
首次运行需要能访问 pip 包源；安装失败会显示原因并等待按键，不启动程序。

也可以手动执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python main.py
```

程序启动后会显示托盘就绪提示。右键托盘图标可快速截图、编辑剪贴板图片、打开图片、进入设置或退出；双击托盘图标开始自由选区截图。

依赖清单见 [requirements.txt](requirements.txt)。全局热键依赖 `keyboard`；Windows 的窗口边缘吸附还使用 `pywin32`。

## 操作手册

### 截图

- `F1` 开始自由选区。可在一个遮罩中创建多个矩形，按 Enter 或双击确认；未选区时按 Esc 直接退出截图。
- `Ctrl+Shift+F1` 选择整个虚拟桌面；`Ctrl+F1` 选择鼠标所在的当前显示器。
- `Ctrl+Shift+F2` 重用上次选区的位置和大小，在新画面中重新截图。显示器布局变化后若选区已不在桌面范围内，程序会提示。
- `Ctrl+F` 打开固定尺寸选区输入框；输入宽高后，在当前鼠标位置建立选区。
- 拖动选区内部可移动；拖动四角或边缘中点可调整大小。选区接近屏幕或可见窗口边缘时会吸附。选中选区后使用方向键或 WASD 可微调位置。
- 多显示器截图使用虚拟桌面坐标，支持显示器负坐标；显示器之间的间隙也处于虚拟桌面矩形内。
- 截图提示显示首个选区的缩略图和选区总数，约 4 秒后关闭。提示、尺寸文字、十字线和放大镜不会写入截图。
- 单选区且完整位于同一显示器内时，默认进入**原地编辑**：不会打开新编辑窗口，而是在选区上直接显示编辑画布和紧凑图标工具栏。多选区或跨屏选区会自动回退到独立编辑器窗口，避免多画布和跨屏 DPI 坐标冲突。
- 进入原地编辑后不能继续新增选区，但可以拖动已有选区的角点或边中点调整大小；调整时会隐藏旧编辑层，释放鼠标后按新区域重新裁剪并重建编辑画布。调整选区会清空该区域已有标注，避免标注坐标与新截图区域错位。
- 原地编辑会立即保存一次初始截图；点击保存或双击空白会覆盖同一个文件并退出；放弃会关闭编辑层但保留初始截图文件。
- 未选区、调整选区和原地编辑时均可显示放大镜；原地编辑按 Esc 直接退出本次截图，不返回选区状态。“显示鼠标”开关只在本次截图存在带/不带鼠标两版时可用。
- 原地编辑不提供裁剪工具；需要裁剪图片时可在独立编辑窗口中使用。取色后紧凑工具栏的颜色按钮仍只显示色块。

遮罩深浅、锚点样式、十字线颜色和宽度、放大镜、光标捕获及通知均可在设置中调整。遮罩不透明度 0% 表示不遮盖选区外区域，100% 表示完全遮盖。

### 编辑器

#### 标注工具

标注工具包括选择、画笔、记号笔、文字、箭头、矩形、椭圆、橡皮擦、马赛克、取色和裁剪。编辑器默认大小为 1200×760。标注颜色位于标注区域第二个控件；“显示鼠标”位于标注区域第二行第一个位置。单选区原地编辑使用紧凑图标工具栏，按钮名称与说明通过多行 tooltip 展示。

“更多设置”随当前工具显示对应参数：

| 工具 | 可调整参数 | 说明 |
| --- | --- | --- |
| 画笔 | 独立线宽 | 1–50 px，只影响之后新建的画笔线条 |
| 矩形、椭圆 | 独立线宽、实线/虚线线型 | 使用公共标注颜色；可对已选矩形/椭圆即时切换线型 |
| 箭头 | 线宽、实心/空心/双向/线段/虚线样式 | 箭头方向由拖动方向决定；线段类型无箭头，虚线类型复用当前线宽和颜色 |
| 记号笔 | 独立线宽、不透明度 | 不透明度 1%–100%；线条比普通画笔更粗，适合高亮重点 |
| 文字 | 字体、字号、对齐 | 字号 6–200 pt；支持左对齐、居中、右对齐 |
| 马赛克 | 方块/毛玻璃/细粒、颗粒度 | 作用于框选区域 |
| 橡皮擦 | 直径 | 只擦除标注，不会擦除原始截图像素 |
| 裁剪 | 裁剪框颜色、线宽 | 仅独立编辑器提供；参数持久化到配置文件 |

画笔、形状、箭头、记号笔和文字共用标注颜色；各工具线宽、形状线型、文字样式、箭头样式、马赛克选项及裁剪框颜色和线宽均通过配置文件持久化。首次没有配置时标注颜色默认 `#ff0000`，各标注工具线宽默认 2 px，橡皮擦直径 30 px，马赛克颗粒度 10 px；已有配置优先。
旧配置中的“双向”样式仍按空心双向显示；双向箭头的两端使用相同的尖角比例。编辑器导出时对标注边缘启用抗锯齿，以减轻斜线锯齿。

- 选择工具可单击标注选中并拖动；在空白处拖框可多选。选中标注后可移动、调整大小、改变颜色/线宽；悬停标注显示四向移动光标，悬停右下角缩放手柄显示对角缩放光标。Delete 删除所选标注。
- 在其他工具下双击已有标注可快速切到选择工具并显示选中框和缩放手柄；在选择工具下双击已有标注直接删除该标注，可撤销。右键单击标注可选择“删除标注”，文字标注还可选择“编辑文字”。
- 双击空白画布保存并退出；双击标注不会结束编辑。Esc 放弃编辑并关闭窗口。
- 画笔、形状和箭头在拖动时显示预览。裁剪/马赛克框的预览不会直接作为标注导出；确认裁剪会改变底图并清除标注，撤销可恢复。
- 取色工具选出的颜色会设为当前标注颜色并复制十六进制颜色值。

#### 浏览、缩放和变换

- 右键或中键拖动画布，或按住空格拖动，可平移画布；右键单击已有标注则打开标注菜单。
- 鼠标滚轮默认上下滚动；按住 Ctrl 或 Alt 滚轮改为横向移动。纵向、横向滚动步长均已调小；滚轮不缩放画布，也不缩放选中标注。
- 画布显示缩放通过编辑器下方的滑块或百分比输入控制，范围为 1%–800%。显示缩放不改变导出图片分辨率。
- 图像旋转区域的第一个按钮是“重置角度”：应用任意角度旋转后可恢复旋转前状态；编辑图像或标注后该入口会禁用。左右旋转 90°、旋转 180°、水平/垂直翻转及任意角度旋转也在该区域。
- 任意角度旋转支持旋钮预览和数字精确输入；取消恢复原状态，确认后记录为一次可撤销操作。旋转和翻转会同步变换现有标注。
- 撤销、重做和重置操作位于编辑区域。重置用于恢复原始截图状态。

#### 输出

- **保存**：保存 PNG 到手动保存目录，按保存设置中两个独立开关复制图片和/或文件路径到剪贴板，然后退出编辑器；默认复制图片、不复制路径。
- **贴图**：将当前合成图作为独立置顶窗口打开。
- **放弃**或 Esc：关闭编辑器，不保存当前编辑结果。
- 双击空白画布执行保存操作。保存成功提示可显示最终图片缩略图。

原地编辑中的输出按钮含义更直接：**贴图** 会创建贴图并退出原地编辑；**保存** 会覆盖当前初始文件，按相同剪贴板开关复制内容并退出；双击空白画布执行同样的保存动作。初始自动保存不修改剪贴板，显式点击“复制图片”始终复制图片；**放弃** 会退出但不覆盖修改。单张原地编辑不显示“关闭全部”按钮。

编辑器的滚轮和按键行为也会显示在画布下方的操作提示中；支持当前快捷键的按钮和菜单项会显示绑定提示。

### 贴图窗口

每张贴图都是独立窗口。左键拖动移动；滚轮缩放（倍率限制 0.1–10）；方向键或 WASD 每次移动 1 像素；Esc 关闭当前贴图。贴图支持默认描边和阴影，外观可在设置的“贴图”页统一配置。

右键菜单提供锁定/解锁、重置大小、复制图像、从文件打开替换此贴图、从文件打开新贴图、描边开关、阴影开关、置顶开关、关闭当前贴图、透明度滑块及点击穿透。替换会保留当前窗口的位置与缩放；取消文件选择不会更改贴图。锁定后不能拖动、缩放或用方向键移动。点击穿透后无法从贴图本身打开右键菜单，使用 `Ctrl+Shift+T` 恢复贴图交互。描边/阴影右键菜单只控制当前贴图是否显示，颜色、宽度和强度由全局设置控制。

`F3` 按修改时间从自动保存目录打开最新且尚未贴出的有效 PNG；连续按下会依次寻找更早的未贴图片，全部已贴出时不重复创建。关闭某张贴图后可以再次用 F3 贴出；已删除或无法读取的历史图片会被跳过。上一张/下一张热键仍切换自动目录中的 PNG 历史，并复用历史贴图窗口。隐藏/显示、关闭全部贴图也有全局热键。

### 系统托盘

托盘右键菜单包含：快速截图、编辑剪贴板图片、打开并编辑图片、从文件打开新贴图、设置、退出。菜单项的快捷键文字跟随设置中的当前绑定；快捷键被清除时，不显示过期提示。

## 默认快捷键

| 动作 | 默认快捷键 |
| --- | --- |
| 自由选区截图 | `F1` |
| 上次位置截图 | `Ctrl+Shift+F2` |
| 全屏（虚拟桌面）截图 | `Ctrl+Shift+F1` |
| 当前显示器截图 | `Ctrl+F1` |
| 贴上次截图 | `F3` |
| 编辑剪贴板图片 | `Ctrl+Alt+V` |
| 打开并编辑图片 | `Ctrl+Alt+O` |
| 上一张/下一张历史贴图 | `Ctrl+Alt+Left` / `Ctrl+Alt+Right` |
| 隐藏/显示贴图 | `Ctrl+Shift+H` |
| 关闭全部贴图 | `Ctrl+Shift+X` |
| 恢复贴图交互 | `Ctrl+Shift+T` |
| 固定尺寸选区 | `Ctrl+F` |
| 画布平移 | 右键/中键拖动或按住空格拖动 |

设置窗口的快捷键页可以录制、修改或清除全局热键。录制期间全局热键暂停；相同组合键不能分配给多个动作。设置热键总开关可临时禁用全部全局热键。快捷键由配置文件保存，按钮提示和托盘菜单显示当前绑定。

## 设置和数据

设置窗口分为常规、快捷键、保存、编辑器、日志和贴图六类：
每页按使用场景显示带标题的设置组，较长的页面可在页内滚动；同一组件的颜色、线宽及其他参数集中在同一组。

- **常规**：开机自动启动、光标捕获、放大镜、十字线、遮罩、锚点、通知和声音。
- **快捷键**：全局热键开关、录制、清除和冲突检查。
- **保存**：原地编辑初始截图目录、手动保存目录、文件名模板、保存后打开目录，以及保存时复制图片/文件路径的两个独立开关。
- **编辑器**：默认标注工具、公共标注颜色、线宽、矩形/椭圆线型、字号、字体、文字对齐、箭头/线段样式和马赛克选项。
- **日志**：日志开关、级别、轮转和保存目录设置。
- **贴图**：新贴图默认描边、描边颜色/宽度、默认阴影、阴影颜色/强度。

开机自动启动默认关闭，仅在 Windows 上可用；开启后写入当前用户的登录启动项，不需要管理员权限。旧配置缺少该选项时会补为关闭，但不会在启动时删除用户手动创建的同名启动项。
源码运行时启动项指向当前 Python 和 `main.py`；用 PyInstaller 打包运行时指向当前 `.exe`，不依赖源码。移动打包后的程序后，手动运行新位置的程序一次即可更新启动路径。

配置和运行数据不放在源码目录：

| 数据 | Windows | 其他系统 |
| --- | --- | --- |
| 设置、贴图会话、临时缓存 | `%APPDATA%\ScreenSnap` | `~/.config/ScreenSnap` |
| 运行日志（默认） | 启动文件所在目录的 `logs` | 启动文件所在目录的 `logs` |
| 自动保存与历史图片 | `~/Pictures/ScreenSnap/Auto` | `~/Pictures/ScreenSnap/Auto` |
| 手动保存 | `~/Pictures/ScreenSnap/Manual` | `~/Pictures/ScreenSnap/Manual` |

启动时若设置文件或贴图会话无法解析，会把原文件改名为带 `.broken-` 时间戳的备份；
设置恢复默认值，贴图会话跳过无法恢复的记录。贴图私有缓存仅保留当前有效会话引用的图片，
退出保存会话或下次启动时会清理孤儿缓存；损坏会话对应的缓存暂时保留，便于排查和恢复。

日志保存目录可在日志设置中指定；只有目录已存在时才写入该位置。留空或指定目录不存在时，日志写入启动文件（源码运行时为 `main.py`，打包后为可执行文件）所在目录的 `logs` 子目录，不会尝试创建不存在的自定义目录。

Windows 用户目录按系统返回的图片目录解析。自动和手动目录可分别修改。文件名模板使用 Python `strftime`，默认 `_%Y%m%d_%H%M%S`；模板下方列出年、月、日、时、分、秒占位符及普通字符的含义。重名时追加序号，非法路径字符替换为下划线。自动保存目录同时作为贴图历史 PNG 来源。

配置首次启动时创建；旧配置缺少的新选项会使用默认值补齐。设置页面支持 JSON 导入/导出，导入时校验类型、取值和热键冲突。旧版 `%APPDATA%\SnipasteClone` 数据仅在新 ScreenSnap 目录不存在时迁移。

## 技术指南

### 项目结构

| 模块 | 责任 |
| --- | --- |
| `main.py` | 应用组装、托盘、配置刷新、快捷键分发和窗口生命周期 |
| `config/` | JSON 默认值、迁移、校验、导入/导出 |
| `core/` | 屏幕捕获、路径处理、快捷键标签等共享逻辑 |
| `hotkey/` | 全局键盘监听线程和动作信号 |
| `screenshot/` | 遮罩窗口、多选区状态和截图提示绘制 |
| `editor/` | 工具栏、画布、矢量标注、撤销历史、变换与输出 |
| `sticker/` | 独立贴图窗口、右键菜单、历史图片与会话恢复 |
| `ui/` | 托盘菜单、通知、设置页及共享控件 |
| `logger/` | 标准库日志配置和轮转 |
| `tests/test_core.py` | 离屏 Qt 行为测试 |

### 关键数据流

1. `Application` 加载 `ConfigManager`，创建设置窗口、托盘和 `HotkeyManager`。
2. `HotkeyManager` 在后台线程注册 `keyboard` 热键；触发时发出 Qt 信号，由主线程 `Application.dispatch()` 执行动作。
3. 截图捕获返回图像、虚拟桌面边界和显示器信息；`MaskWindow` 管理选区。单选区单屏内默认进入 `InlineEditor` 原地编辑；多选区或跨屏选区传给 `EditorWindow`。
4. `AnnotationCanvas` 持有底图和可编辑图元。绘制预览与最终图元尽量共用工厂/参数；`render_image()` 只导出底图和场景标注，不导出视图辅助提示。
5. `EditorWindow` 发出保存或贴图信号；`Application` 将结果交给保存通知或 `StickerManager`。
6. 设置变更先经 `ConfigManager` 校验并保存，再刷新热键、托盘文字、已打开编辑器中的配置型控件，以及已打开贴图的外观。裁剪框颜色/宽度是当前编辑器局部状态，不进入该持久化链路。
7. 贴图创建时会把无源图片立即写入 `sticker_cache`，运行期主要持有显示用 `QPixmap` 和源文件路径；退出时保存贴图会话状态，重启后从源文件恢复。

### 修改功能时的检查清单

- 找出实际决定行为的模块，并检查已有调用方和对应测试。
- 同一改动更新默认配置/校验、设置页面、运行时同步和界面提示中涉及的所有部分。
- 更新本 README 对应的操作步骤、快捷键、默认值或限制；删除已经失效的说明。
- 为用户可见行为增加或调整回归测试。布局改动覆盖窄、中、宽窗口；配置改动同时验证保存和重开恢复。
- 运行聚焦测试，再运行完整离屏测试；真实显示器、全局热键权限和点击穿透仍需设备验收。

## 测试与打包

在项目目录执行离屏测试：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python -m unittest discover -s tests -v
Remove-Item Env:QT_QPA_PLATFORM
```

仅运行一个测试类方法时，例如：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python -m unittest tests.test_core.CoreTests.test_arrow_styles_include_filled_open_and_double -v
```

测试覆盖配置校验、热键录制、选区、编辑器工具和布局、箭头几何、裁剪、保存/剪贴板、贴图菜单与会话。离屏测试不能取代多显示器、混合 DPI、全局热键权限及真实桌面点击穿透验收。

## PyInstaller 打包

当前以 PyInstaller 为打包方式，它已包含在 [requirements.txt](requirements.txt) 中。Nuitka 暂有兼容问题，不在依赖清单中；如需使用，请单独安装并重新验证。

先按快速开始安装依赖，再选择文件夹或单文件、带控制台或无控制台模式构建。调试时优先使用带控制台模式。

### 文件夹模式 + 无控制台（成品）

```powershell
.\.venv\Scripts\pyinstaller --noconfirm --clean --onedir --windowed --icon icon.ico --name ScreenSnap --hidden-import ui.settings_window --hidden-import ui.tray_menu --hidden-import ui.capture_notification main.py
```

### 文件夹模式 + 带控制台（调试）

```powershell
.\.venv\Scripts\pyinstaller --noconfirm --clean --onedir --console --icon icon.ico --name ScreenSnap --hidden-import ui.settings_window --hidden-import ui.tray_menu --hidden-import ui.capture_notification main.py
```

### 单文件模式 + 无控制台

```powershell
.\.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --icon icon.ico --name ScreenSnap --hidden-import ui.settings_window --hidden-import ui.tray_menu --hidden-import ui.capture_notification main.py
```

### 单文件模式 + 带控制台（调试）

```powershell
.\.venv\Scripts\pyinstaller --noconfirm --clean --onefile --console --icon icon.ico --name ScreenSnap --hidden-import ui.settings_window --hidden-import ui.tray_menu --hidden-import ui.capture_notification main.py
```

产物为 `dist\ScreenSnap\ScreenSnap.exe`（文件夹模式，分发时拷贝整个 `dist\ScreenSnap` 目录）或 `dist\ScreenSnap.exe`（单文件模式）。

`--onedir` 生成可整体分发的文件夹，`--onefile` 生成单个可执行文件；`--console` 便于查看启动错误，`--windowed` 隐藏控制台。

首次打包请**先选带控制台的两组中之一**构建，确认没有 traceback 后再出窗口版。

### PyInstaller 常见报错与解决

| 报错/现象 | 原因 | 解决办法 |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'ui.settings_window'`（或 `ui.tray_menu`、`ui.capture_notification`） | `ui/__init__.py` 的 `__getattr__` 用 `importlib.import_module` 懒加载顶层组件，PyInstaller 只做静态 import 分析扫描不到；这三个模块在仓库里没有任何静态引用（只有 `ui.widgets.tooltip` 被 `main.py` 直接导入） | 加上面三个 `--hidden-import`；或改用 `--collect-submodules=ui` 一把全收；也可以在入口 `main.py` 顶部改为显式 `from ui.settings_window import SettingsWindow` 等静态导入（代价是可能重新引入循环引用） |
| `ImportError: DLL load failed while importing _ctypes` / `IMPORT_HARD_CTYPES` | 使用 Conda/Miniconda 派生的 Python 时，libffi 被改名成 `ffi.dll` 且放在环境的 `Library\bin` 下，没被打进包 | 换成 python.org 官方 CPython（推荐）；或用 `--paths "<环境>\Library\bin"` 让 PyInstaller 找到 DLL，必要时 `--add-binary "<环境>\Library\bin\ffi.dll;."` |
| `ImportError: DLL load failed while importing win32ui/win32gui` / `No module named pywintypes` | pywin32 的运行时 DLL（`pywintypes312.dll`、`pythoncom312.dll`）位于 `site-packages\pywin32_system32`，虚拟环境里未执行 post-install 时不会落地到正确位置 | 确认 `.venv\Lib\site-packages\pywin32_system32` 下有这两个 DLL；缺失则重装：`.\.venv\Scripts\python -m pip uninstall pywin32` 后重装，或运行 `python Scripts\pywin32_postinstall.py -install`；仍不行则补 `--hidden-import pywintypes --hidden-import pythoncom` |
| `This application failed to start because no Qt platform plugin could be initialized.` / 双击无任何窗口直接退出 | Qt 的 `platforms\qwindows.dll` 未随行；PySide6 6.11 较新，旧版 PyInstaller 的 hook 可能不完整 | 升级到 PyInstaller 6.x 以上再打包；或加 `--collect-all PySide6`；临时验证可把 `.\.venv\Lib\site-packages\PySide6\plugins\platforms` 拷到 exe 同目录，并设置 `QT_QPA_PLATFORM_PLUGIN_PATH` |
| 窗口版闪退看不到任何信息 | `--windowed` 吞掉了 stdout/stderr | 去掉 `--windowed`（或加 `--console`、`--debug=all`）重新构建，在 exe 所在目录用终端运行查看堆栈；未自定义日志目录时，程序还会把日志写到 exe 同级的 `logs\app.log` |
| `PermissionError` / 无法删除 `dist`、`build` 或 exe 被占用 | 上一次构建的托盘程序仍在运行 | 托盘右键退出（或任务管理器结束 `ScreenSnap.exe`）后重试；重复构建保留 `--noconfirm`，改依赖后务必保留 `--clean` |
| 单文件版首次启动要几秒才出现托盘、被杀软拦截 | onefile 每次运行都把内容解压到 `%TEMP%\_MEIxxxx`，解压行为常被误报 | 改用 onedir 分发；或把生成的 `dist\ScreenSnap.exe` 加入杀软白名单 |
| 打包后热键不触发，源码运行正常 | `keyboard` 的全局钩子对不同权限等级的进程无效，和是否打包无关 | 以管理员身份运行，或对目标进程保持同级权限；详见「已知限制」 |
| 开机自动启动写了源码路径 | 自查是否从源码运行；PyInstaller 同样会设置 `sys.frozen`，`core/startup.py` 会写入 exe 路径 | 用打包后的 exe 手动启动一次即可同步注册表；移动位置后再启动一次同样会更新 |

PyInstaller 当前为推荐打包方式；窗口版出现启动问题时先用带控制台模式定位错误。

一般不需要管理员权限；只有明确需要操作受保护窗口时才考虑提升权限。Windows 全局热键在权限不同的应用上可能无法触发。

## 已知限制

- 混合 DPI 多显示器的物理像素映射需在目标设备验证。
- 系统光标无法读取时，捕获光标与未捕获光标的两种截图可能相同。
- 原地编辑首版只支持单选区且完整位于一个显示器内；多选区和跨屏选区会自动回退到完整编辑窗口。
- 原地编辑首版隐藏图像旋转组，避免旋转/翻转改变画布尺寸后引发选区和 DPI 坐标错位。
- 橡皮擦只移除标注，不擦除原始截图像素；马赛克和裁剪需要框选区域。
- 贴图历史只读取自动保存目录中的 PNG，没有独立图库或历史文件删除界面。
- 点击穿透依赖操作系统窗口输入行为；恢复交互通过全局快捷键完成。
