param(
    [string]$Hostname = "stock.autoaistock.top",
    [string]$TunnelName = "autoaistock",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ToolsDir = Join-Path $ProjectRoot ".tools"
$Cloudflared = Join-Path $ToolsDir "cloudflared.exe"
$DataDir = Join-Path $ProjectRoot "data"
$ConfigPath = Join-Path $DataDir "cloudflared-fixed.yml"
$CloudflaredHome = Join-Path $env:USERPROFILE ".cloudflared"
$OriginCert = Join-Path $CloudflaredHome "cert.pem"

New-Item -ItemType Directory -Force -Path $ToolsDir, $DataDir, $CloudflaredHome | Out-Null

if (-not (Test-Path $Cloudflared)) {
    Write-Host "Downloading cloudflared..."
    $Url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    Invoke-WebRequest -Uri $Url -OutFile $Cloudflared
}

Write-Host ""
Write-Host "Checking public DNS for $Hostname ..."
$RootDomain = ($Hostname -split "\.", 2)[1]
try {
    Resolve-DnsName $RootDomain -Type NS -Server 8.8.8.8 -ErrorAction Stop | Out-Null
}
catch {
    Write-Host ""
    Write-Host "WARNING: $RootDomain is not visible in public DNS yet."
    Write-Host "Finish domain real-name/payment status and point its nameservers to Cloudflare first."
    Write-Host "You can still continue after Cloudflare shows the domain as active."
    Write-Host ""
}

if (-not (Test-Path $OriginCert)) {
    Write-Host ""
    Write-Host "Cloudflare login is required. A browser window will open."
    Write-Host "Choose the Cloudflare account that contains $RootDomain, then authorize cloudflared."
    Write-Host ""
    & $Cloudflared tunnel login
}

if (-not (Test-Path $OriginCert)) {
    throw "Cloudflare login did not create $OriginCert. Run this script again after logging in."
}

$ListJson = & $Cloudflared tunnel list --output json
$TunnelList = @()
if ($ListJson) {
    $TunnelList = $ListJson | ConvertFrom-Json
}

$Tunnel = $TunnelList | Where-Object { $_.name -eq $TunnelName } | Select-Object -First 1
if (-not $Tunnel) {
    Write-Host "Creating tunnel $TunnelName ..."
    & $Cloudflared tunnel create $TunnelName
    $ListJson = & $Cloudflared tunnel list --output json
    $TunnelList = $ListJson | ConvertFrom-Json
    $Tunnel = $TunnelList | Where-Object { $_.name -eq $TunnelName } | Select-Object -First 1
}

if (-not $Tunnel) {
    throw "Could not find or create tunnel $TunnelName."
}

$TunnelId = $Tunnel.id
$CredentialsPath = Join-Path $CloudflaredHome "$TunnelId.json"
if (-not (Test-Path $CredentialsPath)) {
    throw "Tunnel credentials file was not found: $CredentialsPath"
}

Write-Host "Routing $Hostname to tunnel $TunnelName ..."
& $Cloudflared tunnel route dns $TunnelName $Hostname

$CredentialsYamlPath = $CredentialsPath -replace "\\", "/"
$ServiceUrl = "http://127.0.0.1:$Port"
$Config = @"
tunnel: $TunnelId
credentials-file: '$CredentialsYamlPath'

ingress:
  - hostname: $Hostname
    service: $ServiceUrl
  - service: http_status:404
"@

Set-Content -Path $ConfigPath -Value $Config -Encoding utf8

Write-Host ""
Write-Host "Fixed domain tunnel configured."
Write-Host "Hostname: https://$Hostname"
Write-Host "Config: $ConfigPath"
Write-Host ""
Write-Host "Next time, run:"
Write-Host "  run_web_domain.bat"
