# Build script for Clawdmeter (Windows build).
# Creates a venv, installs deps, and produces dist/Clawdmeter.exe.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

# $ErrorActionPreference does NOT apply to native executables — a failing
# pip or pyinstaller sets $LASTEXITCODE and the script sails on. That is not
# theoretical: PyInstaller once failed with a PermissionError (a running
# Clawdmeter.exe held the output file), and this script went on to print
# "Built:" and a SHA-256 of the STALE exe from the previous run. A broken
# build looked exactly like a good one, and the hash published beside a
# release would have been wrong. build.sh and build-macos.sh get this for
# free from `set -euo pipefail`; on PowerShell it has to be explicit.
function Invoke-Checked {
    param([string]$What, [scriptblock]$Cmd)
    & $Cmd
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path .venv)) {
    Invoke-Checked "venv creation" { py -3 -m venv .venv }
}

Invoke-Checked "pip upgrade"       { & .\.venv\Scripts\python.exe -m pip install --upgrade pip }
Invoke-Checked "requirements"      { & .\.venv\Scripts\pip.exe install -r requirements.txt }
Invoke-Checked "pyinstaller install" { & .\.venv\Scripts\pip.exe install pyinstaller==6.20.0 }

$startedAt = Get-Date
Invoke-Checked "pyinstaller" { & .\.venv\Scripts\pyinstaller.exe --clean Clawdmeter.spec }

# Publish a SHA-256 alongside the exe. Upload this .sha256 with the GitHub
# release (and/or paste the hash into the notes) so the in-app update check can
# verify a downloaded build before swapping it in. Format: "<hash>  <name>",
# which update_check.extract_sha256() reads back.
$exe = "$root\dist\Clawdmeter.exe"

# Belt and braces: even on a clean exit, refuse to hash an exe this run did not
# write. Catches a future build step that reports success without producing an
# artifact — the same class of failure, one layer down.
if (-not (Test-Path $exe)) {
    throw "pyinstaller reported success but $exe does not exist"
}
if ((Get-Item $exe).LastWriteTime -lt $startedAt) {
    throw ("$exe is older than this build (written " +
           "$((Get-Item $exe).LastWriteTime), build started $startedAt). " +
           "The build did not replace it — is Clawdmeter.exe still running?")
}

$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$exe.sha256" -Value "$hash  Clawdmeter.exe" -Encoding ascii

Write-Output ""
Write-Output "Built:   $exe"
Write-Output "SHA-256: $hash"
