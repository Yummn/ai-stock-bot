param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ToolsDir = Join-Path $ProjectRoot ".tools"
$Cloudflared = Join-Path $ToolsDir "cloudflared.exe"
$LogDir = Join-Path $ProjectRoot "data"
$TunnelLog = Join-Path $LogDir "public_tunnel.log"
$UrlFile = Join-Path $LogDir "public_url.txt"

New-Item -ItemType Directory -Force -Path $ToolsDir, $LogDir | Out-Null

if (-not (Test-Path $Cloudflared)) {
    Write-Host "Downloading cloudflared..."
    $Url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    Invoke-WebRequest -Uri $Url -OutFile $Cloudflared
}

$ExistingServer = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $ExistingServer) {
    $WebExe = Join-Path $ProjectRoot ".venv\Scripts\ai-stock-web.exe"
    Write-Host "Starting AI stock web server on port $Port..."
    Start-Process -FilePath $WebExe -ArgumentList "--host", "0.0.0.0", "--port", "$Port" -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 2
}

Remove-Item -Force $TunnelLog, $UrlFile -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Starting public demo tunnel..."
Write-Host "Keep this window open while demonstrating."
Write-Host ""

$Args = @(
    "tunnel",
    "--no-autoupdate",
    "--url",
    "http://127.0.0.1:$Port",
    "--logfile",
    $TunnelLog,
    "--loglevel",
    "info"
)

$Tunnel = Start-Process -FilePath $Cloudflared -ArgumentList $Args -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden

$PublicUrl = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $TunnelLog) {
        $PublicUrl = Select-String -Path $TunnelLog -Pattern "https://[-a-z0-9]+\.trycloudflare\.com" -AllMatches |
            ForEach-Object { $_.Matches.Value } |
            Select-Object -First 1
        if ($PublicUrl) {
            break
        }
    }
    if ($Tunnel.HasExited) {
        throw "cloudflared exited before a public URL was created. See $TunnelLog"
    }
}

if (-not $PublicUrl) {
    throw "Timed out waiting for public URL. See $TunnelLog"
}

Set-Content -Path $UrlFile -Value $PublicUrl -Encoding utf8

Write-Host "Public demo URL:"
Write-Host $PublicUrl
Write-Host ""
Write-Host "Anyone with this URL can open the demo page while this window is running."
Write-Host "Close this window or press Ctrl+C to stop the public tunnel."
Write-Host ""

try {
    while (-not $Tunnel.HasExited) {
        Start-Sleep -Seconds 2
    }
}
finally {
    if (-not $Tunnel.HasExited) {
        Stop-Process -Id $Tunnel.Id -Force -ErrorAction SilentlyContinue
    }
}
