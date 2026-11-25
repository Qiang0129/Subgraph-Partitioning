param([switch]$SkipOcr)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
foreach ($tool in @('java','mvn','node','npm.cmd')) { if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Missing tool: $tool" } }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    $python = if (Test-Path -LiteralPath 'D:\ProgramData\anaconda3\python.exe') { 'D:\ProgramData\anaconda3\python.exe' } else { (Get-Command python -ErrorAction Stop).Source }
    & $python -c "import sys,struct; assert sys.version_info >= (3,12) and struct.calcsize('P')==8, 'Need 64-bit Python 3.12+'"
    if ($LASTEXITCODE -ne 0) { throw 'Python version check failed' }
    & $python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
$requirements = if ($SkipOcr) { 'agent/requirements.txt' } else { 'agent/requirements-ocr.txt' }
& '.\.venv\Scripts\python.exe' -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Python dependencies failed' }
Push-Location frontend
try { npm.cmd ci --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }; npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' } } finally { Pop-Location }
mvn -B -f backend/pom.xml package
if ($LASTEXITCODE -ne 0) { throw 'Java build failed' }
Write-Output 'Setup complete. Run .\start.ps1'
