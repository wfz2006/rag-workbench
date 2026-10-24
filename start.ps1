param([int]$Port = 8501)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Missing .venv. Run uv sync --python 3.12 --extra dev first. See README.md.'
}
& $projectPython -m streamlit run app.py --server.port $Port
exit $LASTEXITCODE

