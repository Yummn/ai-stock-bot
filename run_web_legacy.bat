@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src"
".venv\Scripts\python.exe" -m ai_stock_bot.web --legacy --host 127.0.0.1 --port 8766
pause
