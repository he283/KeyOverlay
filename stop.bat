@echo off
rem Force-close every key/mouse overlay instance (no hotkey needed).
rem Pure ASCII on purpose: this file must not contain non-ASCII characters.
rem stop.py only needs the standard library, so any Python 3 will do here.
cd /d "%~dp0"
set "PYEXE="
set "PYARG="
if exist "%SystemRoot%\py.exe" (
    set "PYEXE=%SystemRoot%\py.exe"
    set "PYARG=-3"
)
if not defined PYEXE (
    python -c "pass" >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
    echo [KeyOverlay] Python 3 was not found, cannot run stop.py.
    pause
    exit /b 1
)
"%PYEXE%" %PYARG% "%~dp0stop.py"
ping -n 3 127.0.0.1 >nul
