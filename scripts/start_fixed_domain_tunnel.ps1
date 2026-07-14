param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Cloudflared = Join-Path $ProjectRoot ".tools\cloudflared.exe"
$ConfigPath = Join-Path $ProjectRoot "data\cloudflared-fixed.yml"

if (-not (Test-Path $Cloudflared)) {
    throw "cloudflared.exe was not found. Run run_web_domain_setup.bat first."
}

if (-not (Test-Path $ConfigPath)) {
    throw "Fixed domain config was not found. Run run_web_domain_setup.bat first."
}

$ExistingServer = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $ExistingServer) {
    $WebExe = Join-Path $ProjectRoot ".venv\Scripts\ai-stock-web.exe"
    Write-Host "Starting AI stock web server on port $Port ..."
    Start-Process -FilePath $WebExe -ArgumentList "--host", "0.0.0.0", "--port", "$Port" -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 2
}

Write-Host ""
Write-Host "Starting fixed domain tunnel..."
Write-Host "Keep this window open while the public site should be available."
Write-Host ""

& $Cloudflared tunnel --config $ConfigPath run
