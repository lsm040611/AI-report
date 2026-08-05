@echo off
rem ---------------------------------------------------------------
rem  ASCII ONLY. Do not put Korean text in this file.
rem  cmd.exe reads .bat with the system codepage (CP949 here), so
rem  UTF-8 Korean bytes corrupt the parser and break the whole file.
rem ---------------------------------------------------------------
title HR AI Report Engine
cd /d "%~dp0"

echo ==========================================
echo   HR AI Report Engine
echo   folder : %CD%
echo ==========================================
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3.10"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto NOPY

rem A server left over from a previous run would hold port 8000 and
rem make this one fail with "address already in use". Clear it first.
call :FREEPORT

echo Starting server...
echo Your browser will open automatically in a few seconds.
echo.
echo    address :  http://127.0.0.1:8000
echo    stop    :  close this window, or run stop.bat
echo.

start "" /min "%~dp0_openbrowser.bat"

rem No --reload on purpose: the reloader runs a second python process
rem that survives the window being closed with the X button.
%PY% -m uvicorn main:app --port 8000

echo.
echo Server stopped.
call :FREEPORT
pause
exit /b 0

:FREEPORT
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"LISTENING" ^| findstr ":8000 "') do (
    taskkill /F /PID %%p >nul 2>nul
)
exit /b 0

:NOPY
echo [ERROR] Python not found.
echo         Install from python.org and enable "Add python.exe to PATH".
echo.
pause
exit /b 1
