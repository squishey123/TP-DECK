# Build a Defender-friendly TP DECK zip: embeddable CPython + source + TP-DECK.bat
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Init = Get-Content (Join-Path $Root "tp_deck\__init__.py") -Raw
if ($Init -notmatch '__version__\s*=\s*"([^"]+)"') {
    throw "Could not read version from tp_deck\__init__.py"
}
$Version = $Matches[1]

# 64-bit Windows embeddable CPython (signed python.org binaries, not PyInstaller)
$PyVersion = "3.12.10"
$PyZipName = "python-$PyVersion-embed-amd64.zip"
$PyUrl = "https://www.python.org/ftp/python/$PyVersion/$PyZipName"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

$Stage = Join-Path $Root "dist\TP-DECK"
$OutZip = Join-Path $Root "dist\TP-DECK-$Version-windows-x64.zip"
$DownloadDir = Join-Path $Root "dist\downloads"

Write-Host "Staging TP DECK $Version → $Stage"

if (Test-Path $Stage) {
    Remove-Item $Stage -Recurse -Force
}
New-Item -ItemType Directory -Path $Stage | Out-Null
New-Item -ItemType Directory -Path $DownloadDir -Force | Out-Null

$PyZip = Join-Path $DownloadDir $PyZipName
if (-not (Test-Path $PyZip)) {
    Write-Host "Downloading $PyUrl"
    Invoke-WebRequest -Uri $PyUrl -OutFile $PyZip
}

$PyDir = Join-Path $Stage "python"
Expand-Archive -Path $PyZip -DestinationPath $PyDir -Force

$Pth = Get-ChildItem $PyDir -Filter "python*._pth" | Select-Object -First 1
if (-not $Pth) {
    throw "embeddable ._pth file not found"
}
$PthText = Get-Content $Pth.FullName -Raw
if ($PthText -notmatch "(?m)^import site") {
    $PthText = $PthText -replace "(?m)^#import site", "import site"
    if ($PthText -notmatch "(?m)^import site") {
        $PthText = $PthText.TrimEnd() + "`r`nimport site`r`n"
    }
}
# Isolated embeddable Python only uses paths listed here. ".." is the
# unzipped app folder (main.py + tp_deck).
if ($PthText -notmatch "(?m)^\.\.") {
    $PthText = $PthText.TrimEnd() + "`r`n..`r`n"
}
Set-Content -Path $Pth.FullName -Value $PthText -NoNewline

$GetPip = Join-Path $DownloadDir "get-pip.py"
if (-not (Test-Path $GetPip)) {
    Write-Host "Downloading get-pip.py"
    Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPip
}

$Python = Join-Path $PyDir "python.exe"
Write-Host "Installing pip + requirements (this can take a few minutes)"
& $Python $GetPip --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "get-pip failed" }

$Pip = Join-Path $PyDir "Scripts\pip.exe"
if (-not (Test-Path $Pip)) {
    throw "pip.exe not found after get-pip"
}
& $Python -m pip install --retries 10 --timeout 120 --no-warn-script-location -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# App files (plain .py — not a packed exe)
Copy-Item (Join-Path $Root "main.py") $Stage
Copy-Item (Join-Path $Root "TP-DECK.bat") $Stage
Copy-Item (Join-Path $Root "requirements.txt") $Stage
Copy-Item (Join-Path $Root "scripts\README-FOR-USERS.txt") (Join-Path $Stage "README.txt")

$Pkg = Join-Path $Stage "tp_deck"
New-Item -ItemType Directory -Path $Pkg | Out-Null
Get-ChildItem (Join-Path $Root "tp_deck") -File | Where-Object {
    $_.Extension -in ".py", ".json" -and $_.Name -ne "sku_location_cache.json"
} | ForEach-Object { Copy-Item $_.FullName $Pkg }

if (Test-Path $OutZip) {
    Remove-Item $OutZip -Force
}
Compress-Archive -Path $Stage -DestinationPath $OutZip -Force

Write-Host ""
Write-Host "Built: $OutZip"
Write-Host "Unzip and double-click TP-DECK.bat"
Write-Host "Publish: see README.md → GitHub Release"
