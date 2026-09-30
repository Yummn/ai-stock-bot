@echo off
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
if not exist ".venv-runtime-check\Scripts\python.exe" (
  echo Missing isolated Python runtime. See README.md.
  exit /b 1
)
".venv-runtime-check\Scripts\python.exe" scripts\supervise_paper.py --port 8772 --data-dir data\v4-intraday-preview --model-python .venv\Scripts\python.exe
