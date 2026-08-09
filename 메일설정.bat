@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Gmail sending setup
echo.
echo   Gmail sending setup - one time only
echo   See docs/mail_gmail_setup.md for the Google Cloud steps
echo.
py -3.10 tools\gmail_setup.py
if errorlevel 1 (
  echo.
  echo   Failed. Read the message above.
)
echo.
pause
