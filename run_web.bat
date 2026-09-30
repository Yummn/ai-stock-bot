@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src"
set "PORT=%~1"
if not defined PORT set "PORT=8771"
".venv\Scripts\python.exe" scripts\launch_investor.py --port %PORT%
pause
