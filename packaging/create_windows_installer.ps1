# Package dist\Tubing Master\ as zip and optional Inno Setup installer (repo root).
param(
    [string]$Version = "",
    [switch]$SkipSetup
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$DistDir = Join-Path $RepoRoot "dist\Tubing Master"
$ExePath = Join-Path $DistDir "Tubing Master.exe"
if (-not (Test-Path $ExePath)) {
    Write-Error "Missing $ExePath — run packaging\build.bat first."
}

if (-not $Version) {
    $pyproject = Join-Path $RepoRoot "pyproject.toml"
    $match = Select-String -Path $pyproject -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($match) { $Version = $match.Matches[0].Groups[1].Value }
    if (-not $Version) { $Version = "0.0.0" }
}

$ZipName = "Tubing-Master-$Version-Windows-x64.zip"
$ZipPath = Join-Path $RepoRoot "dist\$ZipName"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path $DistDir -DestinationPath $ZipPath -CompressionLevel Optimal
Write-Host "Created $ZipPath"

if ($SkipSetup) { return }

$IsccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$Iscc = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    Write-Warning "Inno Setup not found — zip only. Install from https://jrsoftware.org/isinfo.php or: choco install innosetup"
    return
}

$Iss = Join-Path $RepoRoot "packaging\tubing_master.iss"
Push-Location (Join-Path $RepoRoot "packaging")
try {
    & $Iscc "/DMyAppVersion=$Version" "tubing_master.iss"
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }
    $SetupPath = Join-Path $RepoRoot "dist\Tubing-Master-$Version-Windows-x64-Setup.exe"
    Write-Host "Created $SetupPath"
}
finally {
    Pop-Location
}
