[CmdletBinding()]
param([string]$Python = "")

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "Ce script d'installation est destiné à Windows."
}
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Échec de $Command (code $LASTEXITCODE)."
    }
}

Push-Location $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        if ($Python) {
            Invoke-Checked -Command $Python -Arguments @("-m", "venv", ".venv")
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            Invoke-Checked -Command "py" -Arguments @("-3", "-m", "venv", ".venv")
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            Invoke-Checked -Command "python" -Arguments @("-m", "venv", ".venv")
        } else {
            throw "Installez Python 3.11 ou ultérieur depuis python.org, avec Tcl/Tk, puis relancez ce script."
        }
    }
    Invoke-Checked -Command $VenvPython -Arguments @("-c", "import sys; assert sys.version_info >= (3, 11), 'Python 3.11 minimum'; import tkinter")
    Invoke-Checked -Command $VenvPython -Arguments @("-m", "pip", "install", "-r", "requirements.txt")
    Invoke-Checked -Command $VenvPython -Arguments @("-m", "pip", "check")
    Write-Host "Installation terminée. Lancez .\scripts\launch.ps1."
} finally {
    Pop-Location
}
