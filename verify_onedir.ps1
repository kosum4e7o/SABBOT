# Verifies the REAL structure of the PyInstaller ONEDIR output (does not move or copy anything).
param([Parameter(Mandatory = $true)][string]$Dir)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Dir)) { throw "ONEDIR folder missing: $Dir" }
$Dir = (Resolve-Path $Dir).Path

function Find-File([string]$name) {
    Get-ChildItem -Path $Dir -Recurse -File -Filter $name -ErrorAction SilentlyContinue | Select-Object -First 1
}
function Require-File([string]$name) {
    $f = Find-File $name
    if (-not $f) { throw "MISSING in ONEDIR build: $name" }
    Write-Host ("OK  {0,-26} {1}" -f $name, $f.FullName.Substring($Dir.Length + 1))
    return $f
}
function Require-Dir([string]$name) {
    $d = Get-ChildItem -Path $Dir -Recurse -Directory -Filter $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $d) { throw "MISSING folder in ONEDIR build: $name" }
    Write-Host ("OK  {0,-26} {1}" -f $name, $d.FullName.Substring($Dir.Length + 1))
}

Write-Host "== ONEDIR layout =="
Get-ChildItem $Dir | Select-Object -First 30 | ForEach-Object { Write-Host ("   " + $_.Name) }

$exe = Join-Path $Dir "Stream_Activity_Bot.exe"
if (-not (Test-Path $exe)) { throw "MISSING application EXE: $exe" }
Write-Host "OK  Stream_Activity_Bot.exe"

[void](Require-File "python312.dll")
Require-Dir "PySide6"
Require-Dir "shiboken6"
[void](Require-File "shiboken6.abi3.dll")
foreach ($dll in "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Svg.dll") { [void](Require-File $dll) }
[void](Require-File "qwindows.dll")
# In-app stream player (QtWebEngine). Only a warning: without it the app still works and falls back to "open in browser".
foreach ($web in "Qt6WebEngineCore.dll", "QtWebEngineProcess.exe") {
    if (Find-File $web) { Write-Host "OK  $web" } else { Write-Warning "WebEngine file missing: $web - the in-app player will be unavailable" }
}                 # Qt platform plugin: without it the GUI cannot start
$pyd = @(Get-ChildItem -Path $Dir -Recurse -File -Filter "*.pyd" -ErrorAction SilentlyContinue)
if ($pyd.Count -lt 5) { throw "Too few .pyd files ($($pyd.Count)) - incomplete build" }
Write-Host "OK  .pyd files: $($pyd.Count)"
$sizeMb = (Get-ChildItem -Path $Dir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
if ($sizeMb -lt 25) { throw ("ONEDIR build is suspiciously small: {0:N1} MB" -f $sizeMb) }
Write-Host ("OK  ONEDIR size: {0:N1} MB" -f $sizeMb)
Write-Host "ONEDIR verification PASSED"
