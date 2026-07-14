@echo off
setlocal
cd /d "%~dp0"
echo Starting LAN dashboard on http://0.0.0.0:8765
echo On your phone, use the computer's Wi-Fi IP, for example http://192.168.0.103:8765
set "PYTHONPATH=%CD%\src"
".venv\Scripts\python.exe" -m ai_stock_bot.web --host 0.0.0.0 --port 8765
pause
