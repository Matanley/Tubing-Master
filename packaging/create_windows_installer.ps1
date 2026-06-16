# Zip dist\Tubing Master\ for distribution (run from repo root after PyInstaller build).
param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$DistDir = Join-Path $RepoRoot "dist\Tubing Master"
if (-not (Test-Path (Join-Path $DistDir "Tubing Master.exe"))) {
    Write-Error "Missing dist\Tubing Master\Tubing Master.exe — run packaging\build.bat first."
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
