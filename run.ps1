# Eclipse on Windows: sets up the virtualenv the first time (and whenever requirements.txt changes), then
# starts the browser. The Windows version of run.sh.
#
#   .\run.ps1              run with your normal profile
#   .\run.ps1 -Fresh       run with a throwaway profile (wiped each launch)
#   .\run.ps1 -Reset       rebuild the virtualenv from scratch, then run
#   .\run.ps1 -Tests       run the unit tests
#   .\run.ps1 <url>        open a link (in the already-running browser, if there is one)
#
# Needs Python 3.14 from python.org (tick "Add python.exe to PATH" when installing).
# (Double-clicking run.bat does the same as .\run.ps1.)

param([switch]$Fresh, [switch]$Reset, [switch]$Tests, [Parameter(ValueFromRemainingArguments)]$Rest)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $env:LOCALAPPDATA "Eclipse Build\venv"  # (the same place browser/self_update.py looks)
$Python = Join-Path $Venv "Scripts\python.exe"

if ($Reset -and (Test-Path $Venv)) { Remove-Item -Recurse -Force $Venv }
if ($Fresh) {
    $env:ECLIPSE_DATA_DIR = Join-Path $env:TEMP "eclipse-fresh"
    if (Test-Path $env:ECLIPSE_DATA_DIR) { Remove-Item -Recurse -Force $env:ECLIPSE_DATA_DIR }
}

if (-not (Test-Path $Python)) {
    Write-Host "Setting up the virtualenv in $Venv ..."
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { & py -3 -m venv $Venv } else { & python -m venv $Venv }
    if (-not (Test-Path $Python)) { throw "Couldn't make a virtualenv: install Python 3.12+ from python.org first." }
}

$Requirements = Join-Path $Root "requirements.txt"
$Hash = (Get-FileHash -Algorithm SHA1 $Requirements).Hash.ToLower()
$Stamp = Join-Path $Venv ".requirements-hash"
if (-not (Test-Path $Stamp) -or (Get-Content $Stamp -Raw).Trim() -ne $Hash) {
    Write-Host "Installing requirements ..."
    & $Python -m pip install --quiet --disable-pip-version-check -r $Requirements
    if ($LASTEXITCODE -ne 0) { throw "Installing the requirements failed." }
    Set-Content -Path $Stamp -Value $Hash -NoNewline
}

Set-Location $Root
$env:PYTHONUTF8 = "1"  # (text files in UTF-8, as on a Mac: otherwise an emoji in a tab title stops saving)
if ($Tests) {
    & $Python -m pip install --quiet --disable-pip-version-check -r (Join-Path $Root "requirements-dev.txt")
    & $Python -m pytest tests -q
    exit $LASTEXITCODE
}
& $Python main.py @Rest
