@echo off
rem Build a single-file KeyOverlay.exe with PyInstaller.
rem The SVG is bundled as data, so the exe works on its own.
rem Pure ASCII on purpose: this file must not contain non-ASCII characters.
cd /d "%~dp0"
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/2] Checking PyInstaller ...
"%PY%" -m pip install --quiet --upgrade pyinstaller

echo [2/2] Building ...
"%PY%" -m PyInstaller --noconfirm --onefile --noconsole --clean ^
    --name KeyOverlay ^
    --icon app.ico ^
    --add-data "keyboard-mouse.svg;." ^
    --add-data "keyboard-mouse-extended.svg;." ^
    --add-data "app.ico;." ^
    --exclude-module tkinter --exclude-module numpy --exclude-module matplotlib ^
    keyoverlay.py

echo.
if exist "dist\KeyOverlay.exe" (
    echo Done:  %~dp0dist\KeyOverlay.exe
) else (
    echo Build failed, check the output above.
)
pause
