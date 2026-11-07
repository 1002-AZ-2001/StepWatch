[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "L'enregistrement StepWatch nécessite Windows."
}
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Environnement Python absent. Exécutez d'abord .\scripts\install.ps1."
}
Push-Location $ProjectRoot
try {
    & $VenvPython -m stepwatch
    $AppExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $AppExitCode
