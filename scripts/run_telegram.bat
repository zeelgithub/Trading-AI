@echo off
REM Launcher for the always-on Telegram listener (no admin required).
REM Started hidden at logon by a .vbs shortcut in the Windows Startup folder.
cd /d "C:\Users\zeela\Downloads\Claude-livetradingbot"
".venv\Scripts\python.exe" -m scripts.run_telegram >> "logs\telegram.log" 2>&1
