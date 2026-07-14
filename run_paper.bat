@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\ai-stock-bot.exe" --once
pause
