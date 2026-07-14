@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup_fixed_domain_tunnel.ps1" -Hostname "stock.autoaistock.top" -TunnelName "autoaistock" -Port 8765
pause
