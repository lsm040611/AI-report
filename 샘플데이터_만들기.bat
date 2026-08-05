@echo off
rem ---------------------------------------------------------------
rem  ASCII ONLY inside this file. The filename may be Korean, but
rem  cmd.exe reads the CONTENT with the system codepage, so Korean
rem  text in here would corrupt the parser.
rem ---------------------------------------------------------------
rem Python prints Korean in UTF-8; the console defaults to CP949 and would
rem show it as garbage. Switch the console to UTF-8 before running anything.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

title Make sample data
cd /d "%~dp0"

echo ==========================================
echo   Sample evaluation sheets
echo ==========================================
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3.10"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto NOPY

%PY% make_fixtures.py
if errorlevel 1 goto FAILED

echo.
echo Done. Open the "fixtures" folder next to this file.
echo Upload one of those .xlsx files on the web page.
echo.
pause
exit /b 0

:FAILED
echo.
echo [ERROR] Could not create the sample files.
echo         Run start.bat once first - it installs what is needed.
echo.
pause
exit /b 1

:NOPY
echo [ERROR] Python not found.
echo         Install from python.org and enable "Add python.exe to PATH".
echo.
pause
exit /b 1
