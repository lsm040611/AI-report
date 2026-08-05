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

rem First run on a new PC has no packages installed. Do it here so that
rem double-clicking this file is genuinely all the user has to do.
%PY% -c "import fastapi, uvicorn, openpyxl, sqlalchemy, multipart" >nul 2>nul
if errorlevel 1 (
    echo First run on this PC - installing required packages.
    echo This takes 1-3 minutes. Please wait.
    echo.
    %PY% -m pip install -r requirements.txt
    echo.
    %PY% -c "import fastapi" >nul 2>nul
    if errorlevel 1 goto NOPKG
    echo Packages installed.
    echo.
)

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

:NOPKG
echo [ERROR] Package install failed.
echo         Check your internet connection and run this file again.
echo         If it keeps failing, open a terminal here and run:
echo             python -m pip install -r requirements.txt
echo.
pause
exit /b 1
