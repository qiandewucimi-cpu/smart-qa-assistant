$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python312 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"

Push-Location $repoRoot
try {
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    if (-not (Test-Path -LiteralPath $venvPython)) {
        if (Test-Path -LiteralPath $python312) {
            & $python312 -m venv ".venv"
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3.12 -m venv ".venv"
        } else {
            throw "Python 3.12 was not found. Install it before running this script."
        }
    }

    & $venvPython -m pip install --disable-pip-version-check -r "scripts\requirements.txt"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

    $envFile = Join-Path $repoRoot "scripts\.env"
    if (-not (Test-Path -LiteralPath $envFile)) {
        Copy-Item -LiteralPath "scripts\.env.example" -Destination $envFile
        Write-Host "Created scripts\.env from the public template. Fill in local credentials before live use."
    }

    & $venvPython "scripts\project_acceptance.py"
    if ($LASTEXITCODE -ne 0) { throw "Offline acceptance failed." }

    Write-Host "Environment is ready."
    Write-Host "Live check: .\.venv\Scripts\python.exe scripts\project_acceptance.py --live"
    Write-Host "Start bot : .\.venv\Scripts\python.exe -u scripts\feishu_bot.py"
} finally {
    Pop-Location
}
