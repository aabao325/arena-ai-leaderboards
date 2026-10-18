param(
    [switch]$Upgrade
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ScriptDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$Requirements = Join-Path $ScriptDir 'requirements.txt'

function Invoke-VenvPython {
    param([string[]]$Arguments)
    & $VenvPython @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed (exit code $LASTEXITCODE): $($Arguments -join ' ')"
    }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $Bootstrap = Get-Command python -ErrorAction SilentlyContinue
    if ($Bootstrap) {
        $BootstrapArgs = @('-m', 'venv', $VenvDir)
    } else {
        $Bootstrap = Get-Command py -ErrorAction SilentlyContinue
        if (-not $Bootstrap) {
            throw 'Python 3 is required to create the project environment. Install Python 3.10+ and enable the py launcher, then rerun this script.'
        }
        $BootstrapArgs = @('-3', '-m', 'venv', $VenvDir)
    }

    Write-Host "Creating project Python environment: $VenvDir" -ForegroundColor Cyan
    & $Bootstrap.Source @BootstrapArgs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
        throw 'Could not create the project virtual environment.'
    }
}

Write-Host "Python: $VenvPython" -ForegroundColor Cyan
& $VenvPython -c 'import sys; assert sys.version_info >= (3, 10)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or newer is required.' }
& $VenvPython -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Bootstrapping pip...' -ForegroundColor Yellow
    Invoke-VenvPython @('-m', 'ensurepip', '--upgrade')
}

if ($Upgrade) {
    Invoke-VenvPython @('-m', 'pip', 'install', '--upgrade', 'pip')
}

Write-Host 'Installing project dependencies...' -ForegroundColor Cyan
Invoke-VenvPython @('-m', 'pip', 'install', '-r', $Requirements)

Write-Host 'Downloading the Camoufox browser...' -ForegroundColor Cyan
Invoke-VenvPython @('-m', 'camoufox', 'fetch')

Write-Host 'Checking imports...' -ForegroundColor Cyan
Invoke-VenvPython @('-c', 'from camoufox.async_api import AsyncCamoufox; import jsonschema')
Write-Host ''
Write-Host 'Dependencies are ready. The daily task will use this project environment.' -ForegroundColor Green
Write-Host "To register/update the task: powershell -ExecutionPolicy Bypass -File `"$(Join-Path $ScriptDir 'setup-task.ps1')`""