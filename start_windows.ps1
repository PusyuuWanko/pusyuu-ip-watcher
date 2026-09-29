$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "=== Pusyuu IP Watcher v4 - Windows startup ==="
Write-Host ""

$pythonCommand = $null
$pythonArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCommand = "py"; $pythonArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCommand = "python"; $pythonArgs = @()
}

if (-not $pythonCommand) {
    Write-Host "[ERROR] Python 3 was not found." -ForegroundColor Red
    $answer = Read-Host "Install Python 3 with winget if available? [Y/N]"
    if ($answer -match '^[Yy]$') {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install --id Python.Python.3.13 -e --accept-source-agreements --accept-package-agreements
            if ($LASTEXITCODE -ne 0) { throw "Python installation failed." }
            Write-Host "Python was installed. Close and reopen PowerShell, then run this script again."
            exit 0
        }
    }
    Write-Host "Install Python 3 manually from:"
    Write-Host "https://www.python.org/downloads/windows/"
    Write-Host 'Enable "Add python.exe to PATH" during installation.'
    exit 1
}

Write-Host "Using: $pythonCommand $($pythonArgs -join ' ')"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating Python virtual environment..."
    & $pythonCommand @pythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
}

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example."
}

Write-Host "Installing/updating dependencies..."
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

Write-Host ""
Write-Host "Starting server..."
Write-Host "Open http://127.0.0.1:8000/ in your browser."
Write-Host "Guide: http://127.0.0.1:8000/guide"
Write-Host "Press Ctrl+C to stop."
Write-Host ""
& ".\.venv\Scripts\python.exe" main.py --host 127.0.0.1 --port 8000
exit $LASTEXITCODE
