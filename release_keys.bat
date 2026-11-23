@echo off
rem Release keys / mouse buttons that are stuck in the "pressed" state.
rem Use when typing triggers shortcuts, or the scroll wheel zooms instead of scrolling.
rem This only sends "key up" events - it never presses anything, so it is safe to double-click.
rem Pure ASCII on purpose: this file must not contain non-ASCII characters.
rem release_keys.py only needs the standard library, so any Python 3 will do here.
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
    echo [KeyOverlay] Python 3 was not found, cannot run release_keys.py.
    pause
    exit /b 1
)
"%PYEXE%" %PYARG% "%~dp0release_keys.py" %*
ping -n 3 127.0.0.1 >nul
