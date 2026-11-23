@echo off
rem KeyMouse overlay - transparent, tilted, click-through. No OBS / NohBoard needed.
rem Pure ASCII on purpose: this file must not contain non-ASCII characters.
rem Hotkeys: show/hide, quit and move are configurable in the control panel.
cd /d "%~dp0"

rem Pick the first Python that can actually import Pillow, and run it windowed:
rem   1) Windows launcher (pyw -3)  2) pythonw on PATH
rem The probe uses py.exe / python because pythonw reports no exit code reliably.
set "PYEXE="
set "PYARG="
if exist "%SystemRoot%\py.exe" (
    "%SystemRoot%\py.exe" -3 -c "import PIL" >nul 2>nul
    if not errorlevel 1 (
        set "PYEXE=%SystemRoot%\pyw.exe"
        set "PYARG=-3"
    )
)
if not defined PYEXE (
    python -c "import PIL" >nul 2>nul
    if not errorlevel 1 set "PYEXE=pythonw"
)
if not defined PYEXE (
    echo [KeyOverlay] Python 3 with Pillow was not found.
    echo Install Python 3 and run:  pip install pillow
    echo Or use the ready-made KeyOverlay.exe from the Releases page.
    pause
    exit /b 1
)

start "" "%PYEXE%" %PYARG% "%~dp0keyoverlay.py" %*
