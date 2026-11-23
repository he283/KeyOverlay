@echo off
rem Build a single-file KeyOverlay.exe with PyInstaller.
rem The SVG is bundled as data, so the exe works on its own.
rem Pure ASCII on purpose: this file must not contain non-ASCII characters.
cd /d "%~dp0"
set "PY=D:\python\python.exe"
if not exist "%PY%" set "PY=python"

rem Old exe running = Windows keeps the file locked, PyInstaller cannot replace it.
tasklist /FI "IMAGENAME eq KeyOverlay.exe" 2>nul | find /I "KeyOverlay.exe" >nul
if not errorlevel 1 (
    echo [0/2] Closing running KeyOverlay.exe ...
    taskkill /IM KeyOverlay.exe /F >nul 2>&1
    ping -n 2 127.0.0.1 >nul
)

echo [1/2] Checking PyInstaller ...
"%PY%" -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 goto :fail

echo [2/2] Building ...
"%PY%" -m PyInstaller --noconfirm --onefile --noconsole --clean ^
    --name KeyOverlay ^
    --icon app.ico ^
    --add-data "keyboard-mouse.svg;." ^
    --add-data "keyboard-mouse-extended.svg;." ^
    --add-data "keyboard-mouse-wide.svg;." ^
    --add-data "app.ico;." ^
    --exclude-module tkinter --exclude-module numpy --exclude-module matplotlib ^
    keyoverlay.py
if errorlevel 1 goto :fail

echo.
echo Done:  %~dp0dist\KeyOverlay.exe
pause
exit /b 0

:fail
echo.
echo Build failed, check the output above.
echo If it still says WinError 5, close KeyOverlay.exe manually (run as administrator if needed) and retry.
pause
exit /b 1
