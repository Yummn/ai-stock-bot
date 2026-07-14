$ErrorActionPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force

Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*start_public_demo.ps1*" -and $_.ProcessId -ne $PID } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Remove-Item -Force (Join-Path $ProjectRoot "data\public_url.txt") -ErrorAction SilentlyContinue

Write-Host "Public demo tunnel stopped."
