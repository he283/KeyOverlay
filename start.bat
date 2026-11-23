@echo off
rem KeyMouse overlay - transparent, tilted, click-through. No OBS / NohBoard needed.
rem Pure ASCII on purpose: this file must not contain non-ASCII characters.
rem F9 = show/hide, F10 = quit (the window is click-through, keyboard is the only way).
cd /d "%~dp0"
set "PYW=D:\python\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0keyoverlay.py" %*
