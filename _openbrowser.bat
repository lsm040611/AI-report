@echo off
rem ASCII ONLY - see start.bat for why.
rem Helper launched by start.bat: waits for the server, then opens the browser.
timeout /t 6 /nobreak >nul
start "" http://127.0.0.1:8000
exit
