# Install -> verify files -> dependency self-test -> start app -> uninstall.  Any failure = exit 1.
param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$InstallDir
)
$ErrorActionPreference = "Stop"
$Installer = (Resolve-Path $Installer).Path
$log = Join-Path $env:RUNNER_TEMP "installer.log"
if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }

function Show-InstallLog { if (Test-Path $log) { Write-Host "---- installer log ----"; Get-Content $log -Tail 60 } }

Write-Host "== 1. silent install =="
$p = Start-Process -FilePath $Installer -Wait -PassThru -ArgumentList @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/NOICONS", "/DIR=`"$InstallDir`"", "/LOG=`"$log`"")
if ($p.ExitCode -ne 0) { Show-InstallLog; throw "Installer exit code $($p.ExitCode)" }

Write-Host "== 2. installed files =="
$exe = Join-Path $InstallDir "Stream_Activity_Bot.exe"
if (-not (Test-Path $exe)) { Show-InstallLog; throw "Application EXE not installed: $exe" }
function Require-Installed([string]$name) {
    $f = Get-ChildItem -Path $InstallDir -Recurse -File -Filter $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $f) { throw "Not installed: $name" }
    Write-Host "OK  $name"
}
foreach ($n in "python312.dll", "shiboken6.abi3.dll", "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Svg.dll", "qwindows.dll") { Require-Installed $n }
foreach ($d in "PySide6", "shiboken6") {
    if (-not (Get-ChildItem -Path $InstallDir -Recurse -Directory -Filter $d | Select-Object -First 1)) { throw "Folder not installed: $d" }
    Write-Host "OK  $d\"
}
$unins = Get-ChildItem -Path $InstallDir -File -Filter "unins*.exe" | Select-Object -First 1
if (-not $unins) { throw "Inno Setup uninstaller (unins000.exe) missing" }
Write-Host "OK  $($unins.Name)"

$dataDir = Join-Path $env:RUNNER_TEMP "SABot-data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$env:STREAM_BOT_HOME = $dataDir

Write-Host "== 3. dependency self-test (installed EXE) =="
$selfOut = Join-Path $env:RUNNER_TEMP "selftest.txt"
if (Test-Path $selfOut) { Remove-Item $selfOut -Force }
$t = Start-Process -FilePath $exe -ArgumentList @("--selftest", "`"$selfOut`"") -WorkingDirectory $InstallDir -PassThru
if (-not $t.WaitForExit(90000)) { Stop-Process -Id $t.Id -Force; throw "Self-test timed out" }
if (Test-Path $selfOut) { Get-Content $selfOut | ForEach-Object { Write-Host "   $_" } }
if ($t.ExitCode -ne 0 -or -not (Test-Path $selfOut) -or -not ((Get-Content $selfOut -Raw) -match "SELFTEST_OK")) {
    throw "Installed application failed its dependency self-test (exit $($t.ExitCode))"
}

Write-Host "== 4. start the application =="
$app = Start-Process -FilePath $exe -WorkingDirectory $InstallDir -PassThru
Start-Sleep -Seconds 12
$app.Refresh()
if ($app.HasExited) { throw "Application exited during startup (exit code $($app.ExitCode))" }
Write-Host "OK  process is running (PID $($app.Id))"
Stop-Process -Id $app.Id -Force
Start-Sleep -Seconds 2

Write-Host "== 5. uninstall =="
$u = Start-Process -FilePath $unins.FullName -Wait -PassThru -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
# The Inno uninstaller hands over to a temporary copy of itself: wait until the files are gone.
for ($i = 0; $i -lt 60 -and (Test-Path $exe); $i++) { Start-Sleep -Seconds 1 }
if (Test-Path $exe) { throw "Uninstall did not remove $exe (uninstaller exit $($u.ExitCode))" }
if (Test-Path (Join-Path $InstallDir "python312.dll")) { throw "Uninstall left runtime files behind" }
Write-Host "OK  application removed"
Write-Host "INSTALLER SMOKE TEST PASSED"
