@echo off
REM ---------------------------------------------------------------
REM  WARNING: keep this file ASCII-only. cmd.exe reads .bat in CP949
REM  and Korean text here breaks the whole script.
REM ---------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

py -3.10 ai_bridge.py import
echo.
pause
