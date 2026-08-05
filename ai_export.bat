@echo off
REM ---------------------------------------------------------------
REM  WARNING: keep this file ASCII-only. cmd.exe reads .bat in CP949
REM  and Korean text here breaks the whole script.
REM ---------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

py -3.10 ai_bridge.py export
if errorlevel 1 (
  echo.
  echo [!] export failed. Is the server folder correct?
)
echo.
echo Next: open Claude Code here and say  "handoff_ai folder"
echo Then run  ai_import.bat
echo.
pause
