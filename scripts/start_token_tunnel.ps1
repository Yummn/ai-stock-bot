param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Cloudflared = Join-Path $ProjectRoot ".tools\cloudflared.exe"
$TokenFile = Join-Path $ProjectRoot "data\cloudflared-token.txt"

if (-not (Test-Path $Cloudflared)) {
    throw "cloudflared.exe was not found."
}

if (-not (Test-Path $TokenFile)) {
    throw "Token file was not found: $TokenFile"
}

$TokenPreview = (Get-Content -Raw -Path $TokenFile).Trim()
if ($TokenPreview -match "cloudflared(\.exe)?\s+service\s+install\s+(.+)$") {
    $TokenPreview = $Matches[2].Trim()
    Set-Content -Path $TokenFile -Value $TokenPreview -Encoding ascii
}

if (-not $TokenPreview.StartsWith("eyJ")) {
    throw "Token file does not look like a Cloudflare Tunnel token."
}

$ExistingServer = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $ExistingServer) {
    $WebExe = Join-Path $ProjectRoot ".venv\Scripts\ai-stock-web.exe"
    Write-Host "Starting AI stock web server on port $Port ..."
    Start-Process -FilePath $WebExe -ArgumentList "--host", "0.0.0.0", "--port", "$Port" -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 2
}

Write-Host ""
Write-Host "Starting Cloudflare fixed tunnel by token..."
Write-Host "Keep this window open while the public site should be available."
Write-Host ""

& $Cloudflared tunnel --no-autoupdate --protocol quic --edge-ip-version 4 run --token-file $TokenFile
