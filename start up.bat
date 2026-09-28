@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 goto no_python
python --version >nul 2>&1
if errorlevel 1 goto no_python

if not exist ".venv\Scripts\pythonw.exe" (
    if exist ".venv" (
        echo Removing incomplete virtual environment...
        rmdir /s /q ".venv"
        if errorlevel 1 goto failed
    )
    echo Creating virtual environment...
    python -m venv ".venv"
    if errorlevel 1 goto failed
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto failed
python -m pip install -r "requirements.txt"
if errorlevel 1 goto failed
if not exist ".venv\Scripts\pythonw.exe" goto failed
start "" ".venv\Scripts\pythonw.exe" "main.py"
if errorlevel 1 goto failed
exit /b 0

:no_python
echo [ERROR] Python is not installed or is not available on PATH.
echo [提示] 未检测到 Python，请安装 Python 3.10 或更高版本并添加到 PATH。
pause
exit /b 1

:failed
echo [ERROR] ScreenSnap setup or launch failed. See the error above.
echo [提示] 环境安装或程序启动失败，请检查上方错误信息。
pause
exit /b 1