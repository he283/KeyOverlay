@echo off
rem Build a single-file KeyOverlay.exe with PyInstaller.
rem The SVG is bundled as data, so the exe works on its own.
rem Pure ASCII on purpose: this file must not contain non-ASCII characters.
cd /d "%~dp0"

rem Pick the first Python that can actually import Pillow:
rem   1) Windows launcher (py -3)  2) python / pythonw on PATH
rem Nothing is hardcoded to one machine, so this works after cloning.
set "PYEXE="
set "PYARG="
if exist "%SystemRoot%\py.exe" (
    "%SystemRoot%\py.exe" -3 -c "import PIL" >nul 2>nul
    if not errorlevel 1 (
        set "PYEXE=%SystemRoot%\py.exe"
        set "PYARG=-3"
    )
)
if not defined PYEXE (
    python -c "import PIL" >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
    echo [KeyOverlay] Python 3 with Pillow was not found.
    echo Install Python 3 and run:  pip install pillow
    pause
    exit /b 1
)

rem Old exe running = Windows keeps the file locked, PyInstaller cannot replace it.
rem Use the full path to findstr: a GNU find earlier on PATH would break the pipe.
tasklist /FI "IMAGENAME eq KeyOverlay.exe" 2>nul | "%SystemRoot%\System32\findstr.exe" /C:"KeyOverlay.exe" >nul
if not errorlevel 1 (
    echo [0/2] Closing running KeyOverlay.exe ...
    taskkill /IM KeyOverlay.exe /F >nul 2>&1
    ping -n 2 127.0.0.1 >nul
)

echo [1/2] Checking PyInstaller ...
"%PYEXE%" %PYARG% -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 goto :fail

echo [2/2] Building ...
"%PYEXE%" %PYARG% -m PyInstaller --noconfirm --onefile --noconsole --clean ^
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
