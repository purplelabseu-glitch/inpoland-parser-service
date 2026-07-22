<#
.SYNOPSIS
  Автообновление Cloudflare cookies → scp на VPS → restart + circuit reset.

.DESCRIPTION
  1) node bootstrap_cf.mjs --auto  (Firefox + PROXY_URL из .env)
  2) scp .cache/inpoland-storage.json на VPS
  3) ssh: systemctl restart inpoland-parser + POST /api/v1/circuit/reset

  Заполните блок CONFIG ниже. Нужен SSH-ключ (без пароля в планировщике).

.EXAMPLE
  # Ручной прогон
  powershell -ExecutionPolicy Bypass -File .\scripts\refresh_cookies.ps1

  # Task Scheduler (каждые 2 часа), действие:
  # Program: powershell.exe
  # Arguments: -ExecutionPolicy Bypass -File "C:\path\to\inpoland-parser-service\scripts\refresh_cookies.ps1"
  # «Выполнять только для вошедшего пользователя» — если браузер с окном (headless=false)
#>

$ErrorActionPreference = "Stop"

# ======================== CONFIG (плейсхолдеры) ========================
$RepoDir       = "C:\path\to\inpoland-parser-service"   # клон репо на этой машине
$VpsUser       = "USER"                                 # SSH user на VPS
$VpsHost       = "VPS_HOST"                             # IP или hostname
$VpsServiceDir = "~/inpoland-parser-service"            # каталог сервиса на VPS
$SshKey        = "$env:USERPROFILE\.ssh\id_ed25519"     # путь к приватному ключу
# Если API_KEY пустой — remote возьмёт из .env на VPS (предпочтительно)
$ApiKey        = ""
$LogDir        = Join-Path $RepoDir "logs"
# =======================================================================

$NodeExe = "node"
$Bootstrap = Join-Path $RepoDir "bootstrap_cf.mjs"
$LocalCookies = Join-Path $RepoDir ".cache\inpoland-storage.json"
$RemoteCookies = "${VpsServiceDir}/.cache/inpoland-storage.json"
$Stamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile = Join-Path $LogDir "refresh_cookies_$Stamp.log"

function Write-Log([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $RepoDir ".cache") | Out-Null

if ($VpsUser -eq "USER" -or $VpsHost -eq "VPS_HOST" -or $RepoDir -like "*path\to*") {
    Write-Error "Заполните CONFIG в scripts/refresh_cookies.ps1 (RepoDir, VpsUser, VpsHost)."
    exit 2
}

if (-not (Test-Path $Bootstrap)) {
    Write-Error "Не найден $Bootstrap"
    exit 2
}

if (-not (Test-Path $SshKey)) {
    Write-Error "Не найден SSH-ключ: $SshKey  (создайте: ssh-keygen -t ed25519)"
    exit 2
}

$sshBase = @(
    "-i", $SshKey,
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new"
)
$sshTarget = "${VpsUser}@${VpsHost}"

Write-Log "=== refresh_cookies start ==="
Write-Log "Repo=$RepoDir  VPS=$sshTarget"

Set-Location $RepoDir

# --- 1) bootstrap --auto ---
Write-Log "1/3 bootstrap_cf.mjs --auto"
$p = Start-Process -FilePath $NodeExe -ArgumentList @("bootstrap_cf.mjs", "--auto") `
    -WorkingDirectory $RepoDir -Wait -PassThru -NoNewWindow `
    -RedirectStandardOutput (Join-Path $LogDir "bootstrap_stdout_$Stamp.txt") `
    -RedirectStandardError  (Join-Path $LogDir "bootstrap_stderr_$Stamp.txt")

Get-Content (Join-Path $LogDir "bootstrap_stdout_$Stamp.txt") -ErrorAction SilentlyContinue | ForEach-Object { Write-Log "  $_" }
Get-Content (Join-Path $LogDir "bootstrap_stderr_$Stamp.txt") -ErrorAction SilentlyContinue | ForEach-Object { Write-Log "  ERR $_" }

if ($p.ExitCode -ne 0) {
    Write-Log "FAIL bootstrap exit=$($p.ExitCode) — scp НЕ делаем (старые cookies на VPS не трогаем)."
    exit $p.ExitCode
}

if (-not (Test-Path $LocalCookies)) {
    Write-Log "FAIL: нет файла $LocalCookies"
    exit 1
}

# --- 2) scp ---
Write-Log "2/3 scp → ${sshTarget}:$RemoteCookies"
& scp.exe @sshBase $LocalCookies "${sshTarget}:$RemoteCookies"
if ($LASTEXITCODE -ne 0) {
    Write-Log "FAIL scp exit=$LASTEXITCODE"
    exit $LASTEXITCODE
}

# --- 3) restart + circuit reset ---
Write-Log "3/3 restart + circuit reset"
$remote = @"
set -e
cd $VpsServiceDir
sudo systemctl restart inpoland-parser
sleep 8
curl -sS --max-time 20 http://127.0.0.1:8001/health || true
KEY='$ApiKey'
if [ -z "`$KEY" ]; then
  KEY=`$(grep -E '^API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '\r')
fi
curl -sS --max-time 20 -X POST http://127.0.0.1:8001/api/v1/circuit/reset -H "X-API-Key: `$KEY"
echo
"@

& ssh.exe @sshBase $sshTarget $remote
if ($LASTEXITCODE -ne 0) {
    Write-Log "FAIL ssh remote exit=$LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Log "=== refresh_cookies OK ==="
exit 0
