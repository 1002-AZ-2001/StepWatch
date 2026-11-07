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

# Vrai si l'interpréteur existe, est en version 3.11 ou ultérieure et fournit
# Tcl/Tk. Toute erreur (lanceur sans cette version, alias du Microsoft Store,
# sortie d'erreur convertie en exception par PowerShell 5.1) vaut « non ».
function Test-SupportedPython {
    param([string]$Command, [string[]]$Arguments = @())
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        return $false
    }
    try {
        & $Command @Arguments -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

Push-Location $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        # Vérifier AVANT de créer .venv : « py -3 » peut désigner un Python
        # trop ancien, et un .venv invalide bloquait ensuite chaque relance.
        $Candidates = @()
        if ($Python) {
            $Candidates += [pscustomobject]@{ Command = $Python; Arguments = @() }
        } else {
            # Préférer les versions antérieures à 3.14 pour les dépendances
            # épinglées ; -Python permet de choisir un autre interpréteur.
            foreach ($Version in @("3.12", "3.13", "3.11")) {
                $Candidates += [pscustomobject]@{ Command = "py"; Arguments = @("-$Version") }
            }
            $Candidates += [pscustomobject]@{ Command = "python"; Arguments = @() }
        }
        $Selected = $null
        foreach ($Candidate in $Candidates) {
            if (Test-SupportedPython -Command $Candidate.Command -Arguments $Candidate.Arguments) {
                $Selected = $Candidate
                break
            }
        }
        if ($null -eq $Selected) {
            if ($Python) {
                throw "$Python n'est pas un Python 3.11 ou ultérieur avec Tcl/Tk."
            }
            throw "Python 3.11 ou ultérieur avec Tcl/Tk introuvable. Installez Python 3.12 depuis python.org, puis relancez ce script ou indiquez -Python <chemin de python.exe>."
        }
        Invoke-Checked -Command $Selected.Command -Arguments ($Selected.Arguments + @("-m", "venv", ".venv"))
    }
    if (-not (Test-SupportedPython -Command $VenvPython)) {
        throw "Le dossier .venv utilise un Python antérieur à 3.11 ou sans Tcl/Tk. Supprimez le dossier .venv, puis relancez ce script."
    }
    Invoke-Checked -Command $VenvPython -Arguments @("-m", "pip", "install", "-r", "requirements.txt")
    Invoke-Checked -Command $VenvPython -Arguments @("-m", "pip", "check")
    Write-Host "Installation terminée. Lancez .\scripts\launch.ps1."
} finally {
    Pop-Location
}
