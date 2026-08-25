# Build the WorldSim desktop .exe (Sprint 54).
# Usage: powershell -File scripts/build_exe.ps1
# Output: dist\worldsim\worldsim.exe

Write-Host "== WorldSim desktop packaging ==" -ForegroundColor Cyan

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "installing pyinstaller + pywebview..."
    pip install pyinstaller pywebview | Out-Null
}
if (-not (Get-Command pywebview -ErrorAction SilentlyContinue)) {
    python -c "import webview" 2>$null
    if ($LASTEXITCODE -ne 0) { pip install pywebview | Out-Null }
}

Write-Host "cleaning previous build..."
if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist\worldsim) { Remove-Item -Recurse -Force dist\worldsim }

Write-Host "running PyInstaller..."
python -m PyInstaller worldsim.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host "BUILD FAILED" -ForegroundColor Red
    exit 1
}

# Ship the local LLM config (model choice) next to the exe when present.
if (Test-Path data\world_sim\llm_config.json) {
    New-Item -ItemType Directory -Force -Path "dist\worldsim\data\world_sim" | Out-Null
    Copy-Item data\world_sim\llm_config.json "dist\worldsim\data\world_sim\" -Force
    Write-Host "llm_config.json shipped with exe"
}

Write-Host ""
Write-Host "done: dist\worldsim\worldsim.exe" -ForegroundColor Green
Write-Host "smoke test: start it, create a world, step, smite, undo."
