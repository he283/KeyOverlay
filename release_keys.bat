@echo off
rem Release keys / mouse buttons that are stuck in the "pressed" state.
rem Use when typing triggers shortcuts, or the scroll wheel zooms instead of scrolling.
rem This only sends "key up" events - it never presses anything, so it is safe to double-click.
rem Pure ASCII on purpose: this file must not contain non-ASCII characters.
cd /d "%~dp0"
set "PY=D:\python\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0release_keys.py" %*
ping -n 3 127.0.0.1 >nul
