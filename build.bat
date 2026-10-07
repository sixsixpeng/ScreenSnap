@echo off
setlocal
chcp 936 >nul
cd /d "%~dp0"

echo ============================================================
echo    ScreenSnap 自动环境检测与构建脚本
echo ============================================================
echo.

:: ---------- 1. 检测 Python ----------
where python >nul 2>&1
if errorlevel 1 goto no_python
python --version >nul 2>&1
if errorlevel 1 goto no_python

echo [检测] Python 已就绪：
python --version
echo.

:: ---------- 2. 创建 / 修复虚拟环境 ----------
if not exist ".venv\Scripts\python.exe" (
    if exist ".venv" (
        echo [提示] 检测到不完整的虚拟环境，正在重建 .venv ...
        rmdir /s /q ".venv"
        if errorlevel 1 goto failed
    )
    echo [1/4] 正在创建虚拟环境 .venv ...
    python -m venv ".venv"
    if errorlevel 1 goto failed
) else (
    echo [1/4] 虚拟环境 .venv 已存在，跳过创建。
)

:: ---------- 3. 安装依赖 ----------
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto failed

echo.
echo [2/4] 正在安装 / 更新依赖（requirements.txt）...
python -m pip install -r "requirements.txt"
if errorlevel 1 goto failed

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 goto no_pyinstaller
echo 依赖就绪，已安装 PyInstaller 版本：
python -m PyInstaller --version

:: ---------- 4. 选择打包方式 ----------
:choose_mode
echo.
echo 请选择打包方式（输入编号后回车）：
echo   [1] 带控制台 + 单文件     调试排错；单一 exe 便于分发，启动较慢
echo   [2] 带控制台 + 目录       调试排错；依赖散落在文件夹，启动快
echo   [3] 不带控制台 + 单文件   面向用户；无黑框、单一 exe，启动较慢
echo   [4] 不带控制台 + 目录     面向用户；无黑框、启动最快（推荐发布）
echo.
set /p "CHOICE=请输入 1-4："

if "%CHOICE%"=="1" goto build_console_onefile
if "%CHOICE%"=="2" goto build_console_onedir
if "%CHOICE%"=="3" goto build_windowed_onefile
if "%CHOICE%"=="4" goto build_windowed_onedir

echo.
echo [错误] 输入无效，请输入 1-4 之间的数字。
goto choose_mode

:: ---------- 各模式打包 ----------
:build_console_onefile
echo.
echo [3/4] 打包：带控制台 + 单文件（--console --onefile）
call :clean_output
python -m PyInstaller --name ScreenSnap --console --onefile --clean --noconfirm --icon ui/assets/icon.ico --add-data "ui/assets;ui/assets" main.py
if errorlevel 1 goto failed
set "RESULT=dist\ScreenSnap.exe"
goto build_done

:build_console_onedir
echo.
echo [3/4] 打包：带控制台 + 目录（--console --onedir）
call :clean_output
python -m PyInstaller --name ScreenSnap --console --onedir --clean --noconfirm --icon ui/assets/icon.ico --add-data "ui/assets;ui/assets" main.py
if errorlevel 1 goto failed
set "RESULT=dist\ScreenSnap\ScreenSnap.exe"
goto build_done

:build_windowed_onefile
echo.
echo [3/4] 打包：不带控制台 + 单文件（--windowed --onefile）
call :clean_output
python -m PyInstaller --name ScreenSnap --windowed --onefile --clean --noconfirm --icon ui/assets/icon.ico --add-data "ui/assets;ui/assets" main.py
if errorlevel 1 goto failed
set "RESULT=dist\ScreenSnap.exe"
goto build_done

:build_windowed_onedir
echo.
echo [3/4] 打包：不带控制台 + 目录（--windowed --onedir）
call :clean_output
python -m PyInstaller --name ScreenSnap --windowed --onedir --clean --noconfirm --icon ui/assets/icon.ico --add-data "ui/assets;ui/assets" main.py
if errorlevel 1 goto failed
set "RESULT=dist\ScreenSnap\ScreenSnap.exe"
goto build_done

:: ---------- 打包前清理旧产物 ----------
:clean_output
echo [清理] 删除上一次的构建产物（避免目录模式与单文件模式互相残留）...
if exist "dist\ScreenSnap.exe" del /q "dist\ScreenSnap.exe" >nul 2>&1
if exist "dist\ScreenSnap\" rmdir /s /q "dist\ScreenSnap" >nul 2>&1
goto :eof

:: ---------- 完成 ----------
:build_done
echo.
echo [4/4] 构建完成！产物位置：
echo   %RESULT%
echo.
echo [提示] 建议在目标 Windows 环境最终验收：全局热键、原生通知与混合 DPI。
echo.
echo [完成] 按任意键关闭此窗口 ...
pause >nul
exit /b 0

:: ---------- 错误处理 ----------
:no_python
echo.
echo [错误] 未检测到 Python，请安装 Python 3.10 或更高版本并勾选 "Add to PATH"，
echo        安装后重新打开本脚本重试。
echo.
pause
exit /b 1

:no_pyinstaller
echo.
echo [错误] PyInstaller 未能正常安装，请检查 requirements.txt 中的 pyinstaller 依赖。
echo        可尝试手动执行：.venv\Scripts\python -m pip install "pyinstaller>=6,<7"
echo.
pause
exit /b 1

:failed
echo.
echo [错误] 构建过程中断，请检查上方错误信息。
echo.
pause
exit /b 1
