@echo off
setlocal
chcp 936 >nul
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

rem ---- 项目根目录（去掉结尾反斜杠）、启动器、入口脚本与图标 ----
set "PROJECT=%~dp0"
if "%PROJECT:~-1%"=="\" set "PROJECT=%PROJECT:~0,-1%"
set "PYTHONW=%PROJECT%\.venv\Scripts\pythonw.exe"
set "MAIN=%PROJECT%\main.py"
set "ICON=%PROJECT%\ui\assets\icon.ico"

echo.
echo [1/2] 正在创建快捷方式（项目目录 + 桌面各一个）...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $desktop = [Environment]::GetFolderPath('Desktop'); $paths = @('%PROJECT%\ScreenSnap.lnk', (Join-Path $desktop 'ScreenSnap.lnk')); foreach ($path in $paths) { $lnk = $ws.CreateShortcut($path); $lnk.TargetPath = '%PYTHONW%'; $lnk.Arguments = [char]34 + '%MAIN%' + [char]34; $lnk.WorkingDirectory = '%PROJECT%'; $lnk.IconLocation = '%ICON%,0'; $lnk.Description = 'ScreenSnap 截图工具'; $lnk.Save(); Write-Host ('      已创建: ' + $path) }"
if errorlevel 1 echo [警告] 快捷方式创建失败，可参照下面的参数手动创建。

echo.
echo       快捷方式参数（两个快捷方式相同）：
echo         目标     : %PYTHONW%
echo         参数     : "%MAIN%"
echo         起始位置 : %PROJECT%
echo         图标     : %ICON%
echo         位置     : %PROJECT%\ScreenSnap.lnk
echo                    桌面\ScreenSnap.lnk
echo       用 pythonw.exe 启动不会弹出黑色控制台窗口；以后直接双击快捷方式即可。

echo.
echo [2/2] 正在启动 ScreenSnap...
start "" "%PYTHONW%" "%MAIN%"
if errorlevel 1 goto failed

echo.
echo [完成] 快捷方式已就绪、程序已启动。按任意键关闭此窗口 ...
pause >nul
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
