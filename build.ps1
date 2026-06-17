# Build script for Clawdmeter-Windows.
# Creates a venv, installs deps, and produces dist/Clawdmeter.exe.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

if (-not (Test-Path .venv)) {
    py -3 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -r requirements.txt
& .\.venv\Scripts\pip.exe install pyinstaller==6.20.0

& .\.venv\Scripts\pyinstaller.exe --clean Clawdmeter.spec

# Publish a SHA-256 alongside the exe. Upload this .sha256 with the GitHub
# release (and/or paste the hash into the notes) so the in-app update check can
# verify a downloaded build before swapping it in. Format: "<hash>  <name>",
# which update_check.extract_sha256() reads back.
$exe = "$root\dist\Clawdmeter.exe"
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$exe.sha256" -Value "$hash  Clawdmeter.exe" -Encoding ascii

Write-Output ""
Write-Output "Built:   $exe"
Write-Output "SHA-256: $hash"
