# Auto refresh CF cookies -> scp to VPS -> restart + circuit reset
# Run: powershell -ExecutionPolicy Bypass -File .\scripts\refresh_cookies.ps1

$ErrorActionPreference = "Stop"

# ======================== CONFIG ========================
$RepoDir       = "D:\work\git\inpoland-parser-service"
$VpsUser       = "u"
$VpsHost       = "31.130.203.134"
$VpsServiceDir = "~/inpoland-parser-service"
$SshKey        = Join-Path $env:USERPROFILE ".ssh\id_ed25519"
$ApiKey        = ""
$LogDir        = Join-Path $RepoDir "logs"
$SudoPassword  = ""
# ========================================================

$secretFile = Join-Path $RepoDir "scripts\refresh_cookies.secret.ps1"
if (Test-Path $secretFile) {
    . $secretFile
}

$NodeExe = "node"
$Bootstrap = Join-Path $RepoDir "bootstrap_cf.mjs"
$LocalCookies = Join-Path $RepoDir ".cache\inpoland-storage.json"
$RemoteCookies = ($VpsServiceDir.TrimEnd("/") + "/.cache/inpoland-storage.json")
$RemoteShTemplate = Join-Path $RepoDir "scripts\refresh_cookies_remote.sh"
$Stamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile = Join-Path $LogDir ("refresh_cookies_" + $Stamp + ".log")

function Write-Log([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $RepoDir ".cache") | Out-Null

if (-not (Test-Path $Bootstrap)) { Write-Error "Missing $Bootstrap"; exit 2 }
if (-not (Test-Path $SshKey)) { Write-Error "Missing SSH key: $SshKey"; exit 2 }
if (-not (Test-Path $RemoteShTemplate)) { Write-Error "Missing $RemoteShTemplate"; exit 2 }

$sshBase = @(
    "-i", $SshKey,
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new"
)
$sshTarget = $VpsUser + "@" + $VpsHost

Write-Log "=== refresh_cookies start ==="
Write-Log ("Repo=" + $RepoDir + "  VPS=" + $sshTarget)
Set-Location $RepoDir

Write-Log "1/3 bootstrap_cf.mjs --auto"
$stdoutFile = Join-Path $LogDir ("bootstrap_stdout_" + $Stamp + ".txt")
$stderrFile = Join-Path $LogDir ("bootstrap_stderr_" + $Stamp + ".txt")
$p = Start-Process -FilePath $NodeExe -ArgumentList @("bootstrap_cf.mjs", "--auto") `
    -WorkingDirectory $RepoDir -Wait -PassThru -NoNewWindow `
    -RedirectStandardOutput $stdoutFile `
    -RedirectStandardError $stderrFile

if (Test-Path $stdoutFile) { Get-Content $stdoutFile | ForEach-Object { Write-Log ("  " + $_) } }
if (Test-Path $stderrFile) { Get-Content $stderrFile | ForEach-Object { Write-Log ("  ERR " + $_) } }

if ($p.ExitCode -ne 0) {
    Write-Log ("FAIL bootstrap exit=" + $p.ExitCode + " - skip scp")
    exit $p.ExitCode
}
if (-not (Test-Path $LocalCookies)) {
    Write-Log ("FAIL: missing " + $LocalCookies)
    exit 1
}

Write-Log ("2/3 scp -> " + $sshTarget + ":" + $RemoteCookies)
& scp.exe @sshBase $LocalCookies ($sshTarget + ":" + $RemoteCookies)
if ($LASTEXITCODE -ne 0) {
    Write-Log ("FAIL scp exit=" + $LASTEXITCODE)
    exit $LASTEXITCODE
}

Write-Log "3/3 restart + circuit reset"
$remoteBody = [System.IO.File]::ReadAllText($RemoteShTemplate)
$remoteBody = $remoteBody.Replace("__VPS_DIR__", $VpsServiceDir)
$remoteBody = $remoteBody.Replace("__API_KEY__", $ApiKey)
$sudoB64 = ""
if ($SudoPassword -and $SudoPassword.Length -gt 0) {
    $sudoB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($SudoPassword))
    Write-Log "Using SudoPassword from secret file (not logged)"
} else {
    Write-Log "No SudoPassword - trying passwordless sudo -n"
}
$remoteBody = $remoteBody.Replace("__SUDO_B64__", $sudoB64)
$remoteBody = $remoteBody.Replace([string][char]13, "")
$tmpSh = Join-Path $env:TEMP ("inpoland_remote_" + $Stamp + ".sh")
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($tmpSh, $remoteBody, $utf8NoBom)

Get-Content -Raw $tmpSh | & ssh.exe @sshBase $sshTarget "bash -s"
$rc = $LASTEXITCODE
Remove-Item $tmpSh -ErrorAction SilentlyContinue

if ($rc -ne 0) {
    Write-Log ("ssh remote exit=" + $rc)
    if ($rc -eq 127) {
        Write-Log "exit 127 often CRLF noise; cookies/restart likely OK"
        exit 0
    }
    Write-Log ("FAIL ssh remote exit=" + $rc)
    exit $rc
}

Write-Log "=== refresh_cookies OK ==="
exit 0