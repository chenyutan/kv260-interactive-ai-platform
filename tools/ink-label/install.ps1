$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskRuntime = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskRuntime)) {
    $taskBundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $taskBundled) {
        & $taskBundled -m venv .venv
    } else {
        py -3.12 -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
}
& $taskRuntime -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $taskRuntime setup_models.py
if ($LASTEXITCODE -ne 0) { throw 'Model download failed.' }
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot 'runtime.txt'), $taskRuntime)
Write-Host 'Ready. Double-click start.vbs.'
