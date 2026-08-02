# Pull automatic Rekaz backups to the main workstation
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $Root "sync_config.json"

if (-not (Test-Path $ConfigPath)) {
  $example = Join-Path $Root "sync_config.example.json"
  if (Test-Path $example) {
    Copy-Item $example $ConfigPath
  }
  Write-Host "Create sync_config.json and set BACKUP_SYNC_TOKEN from /etc/rekaz.env (ops only)."
  exit 2
}

$cfg = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$base = ($cfg.base_url -replace '/$', '')
$token = [string]$cfg.token
$outDirName = if ($cfg.out_dir) { [string]$cfg.out_dir } else { "backups_inbox" }
$keep = if ($cfg.keep_files) { [int]$cfg.keep_files } else { 30 }
$OutDir = Join-Path $Root $outDirName
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Log = Join-Path $Root "sync_log.txt"

function Write-Log([string]$msg) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $Log -Value $line -Encoding UTF8
  Write-Host $line
}

if (-not $token -or $token -match 'PUT_TOKEN_HERE|ضع_هنا') {
  Write-Log "Token missing in sync_config.json"
  exit 3
}

$statusUrl = "$base/api/backups/sync-status?token=$([uri]::EscapeDataString($token))"
$latestUrl = "$base/api/backups/latest?token=$([uri]::EscapeDataString($token))"
$autoUrl = "$base/api/backups/auto-run?token=$([uri]::EscapeDataString($token))"

try {
  try { Invoke-RestMethod -Uri $autoUrl -TimeoutSec 120 | Out-Null } catch {}

  $status = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 120
  $stamp = if ($status.latest -and $status.latest.created_at) {
    ($status.latest.created_at -replace '[:T]', '-' -replace '\..*$','')
  } else {
    Get-Date -Format "yyyyMMdd-HHmmss"
  }
  $dest = Join-Path $OutDir ("rekaz-auto-{0}.zip" -f $stamp)
  $marker = Join-Path $OutDir ".last_pulled_id.txt"
  $lastId = if (Test-Path $marker) { (Get-Content $marker -Raw).Trim() } else { "" }
  $curId = if ($status.latest) { [string]$status.latest.id } else { "" }

  if ($curId -and $curId -eq $lastId -and (Test-Path $dest)) {
    Write-Log "No new backup (id=$curId)"
    exit 0
  }

  Write-Log "Downloading from $base ..."
  Invoke-WebRequest -Uri $latestUrl -OutFile $dest -TimeoutSec 300 -UseBasicParsing
  if ($curId) { Set-Content -Path $marker -Value $curId -Encoding UTF8 }
  $kb = [math]::Round(((Get-Item $dest).Length / 1KB), 1)
  Write-Log "Saved $dest ($kb KB)"

  Get-ChildItem $OutDir -Filter "rekaz-auto-*.zip" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $keep |
    ForEach-Object { Remove-Item $_.FullName -Force }
  exit 0
}
catch {
  Write-Log ("Pull failed: {0}" -f $_.Exception.Message)
  exit 1
}
