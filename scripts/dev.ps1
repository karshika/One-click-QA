# Starts the demo app (:8001), backend (:8000) and frontend (:3000) for local development on Windows.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
# First time: see README "Local setup" (venv + pip install + playwright install + npm install).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Virtualenv not found. Run: python -m venv .venv; .venv\Scripts\pip install -e backend[dev] -e demo-app" }
$env:ALLOW_LOCAL_TARGETS = if ($env:ALLOW_LOCAL_TARGETS) { $env:ALLOW_LOCAL_TARGETS } else { "true" }

$demo = Start-Process -PassThru -NoNewWindow -FilePath $py -ArgumentList "-m","uvicorn","demo_app.main:app","--port","8001" -WorkingDirectory (Join-Path $root "demo-app")
$api = Start-Process -PassThru -NoNewWindow -FilePath $py -ArgumentList "-m","uvicorn","app.main:app","--port","8000" -WorkingDirectory (Join-Path $root "backend")
$web = Start-Process -PassThru -NoNewWindow -FilePath "npm.cmd" -ArgumentList "run","dev" -WorkingDirectory (Join-Path $root "frontend")
Write-Host "Demo app  http://localhost:8001"
Write-Host "Backend   http://localhost:8000/docs"
Write-Host "Dashboard http://localhost:3000   (Ctrl+C to stop)"
try { Wait-Process -Id $api.Id } finally { foreach ($p in $demo, $api, $web) { if ($p -and -not $p.HasExited) { taskkill /F /T /PID $p.Id | Out-Null } } }
