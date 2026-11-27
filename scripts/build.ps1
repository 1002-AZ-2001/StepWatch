[CmdletBinding()]
param([switch]$SingleFile)

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
    # Informations de version et icône dans l'exécutable : un fichier sans éditeur ni
    # version est plus souvent signalé par les antivirus.
    $Resources = Join-Path $ProjectRoot "build\windows"
    Invoke-Checked -Arguments @("scripts\windows_resources.py", "--output", $Resources)
    $Common = @("--noconfirm", "--clean", "--windowed", "--noupx", "--paths", $ProjectRoot,
                "--manifest", (Join-Path $PSScriptRoot "StepWatch.manifest"),
                "--version-file", (Join-Path $Resources "version.txt"), "--icon", (Join-Path $Resources "stepwatch.ico"),
                "--collect-submodules", "pynput", "--add-data", "stepwatch\assets;stepwatch\assets",
                "--exclude-module", "tkinter", "--exclude-module", "_tkinter",
                "--exclude-module", "PySide6.QtNetwork", "--exclude-module", "PySide6.QtDBus")
    Invoke-Checked -Arguments (@("-m", "PyInstaller") + $Common + @("--name", "StepWatch", "stepwatch\__main__.py"))
    if ($SingleFile) {
        Invoke-Checked -Arguments (@("-m", "PyInstaller") + $Common + @("--onefile", "--name", "StepWatch-singlefile", "stepwatch\__main__.py"))
        Write-Host "Exécutable autonome : dist\StepWatch-singlefile.exe"
    }
    Write-Host "Application compilée : dist\StepWatch\StepWatch.exe"
    Write-Host "Distribuez tout le dossier dist\StepWatch, puis effectuez la validation Windows décrite dans le README."
} finally {
    Pop-Location
}
