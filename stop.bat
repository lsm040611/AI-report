@echo off
rem ASCII ONLY - see start.bat for why.
rem Frees port 8000 when a server was left running (window closed with X).
title Stop HR AI Report Engine

set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"LISTENING" ^| findstr ":8000 "') do (
    echo Stopping process %%p ...
    taskkill /F /PID %%p >nul 2>nul
    set FOUND=1
)

if "%FOUND%"=="0" (
    echo Nothing was running on port 8000.
) else (
    echo Done. Port 8000 is free.
)
echo.
pause
