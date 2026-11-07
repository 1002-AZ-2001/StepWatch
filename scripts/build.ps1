[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "Compilez l'exécutable Windows sur Windows ; PyInstaller ne fait pas de compilation croisée."
}
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Exécutez d'abord .\scripts\install.ps1."
}

function Invoke-Checked {
    param([string[]]$Arguments)
    & $VenvPython @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Commande Python en échec (code $LASTEXITCODE)."
    }
}

Push-Location $ProjectRoot
try {
    Invoke-Checked -Arguments @("-m", "pip", "install", "-r", "requirements-dev.txt")
    Invoke-Checked -Arguments @("-m", "unittest", "discover", "-s", "tests", "-v")
    Invoke-Checked -Arguments @("-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "StepWatch", "--paths", $ProjectRoot, "--manifest", (Join-Path $PSScriptRoot "StepWatch.manifest"), "--collect-submodules", "pynput", "stepwatch\__main__.py")
    Write-Host "Application compilée : dist\StepWatch\StepWatch.exe"
    Write-Host "Distribuez tout le dossier dist\StepWatch, puis effectuez la validation Windows décrite dans le README."
} finally {
    Pop-Location
}
