param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$data = Join-Path $PSScriptRoot 'runtime'
New-Item -ItemType Directory -Path $data -Force | Out-Null
$control = Join-Path $data 'processes.json'
foreach ($port in @(8765,8766)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "Port $port is occupied. Stop this project's existing instance with stop.ps1 or resolve the conflict." }
}
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$jar = Join-Path $PSScriptRoot 'backend\target\subfigure-backend-0.1.0.jar'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup.ps1 first' }
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html') -or -not (Test-Path -LiteralPath $jar)) { throw 'Build missing. Run setup.ps1 first' }
$env:SFA_DATA = $data
$env:SFA_LEGACY = Join-Path (Split-Path $PSScriptRoot -Parent) '子图划分结果\result12'
$env:SFA_INTERNAL_TOKEN = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:LANGCHAIN_TRACING_V2 = 'false'
$env:LANGSMITH_TRACING = 'false'
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = 'True'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$processes = @()
try {
    $p = Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','app.main:app','--app-dir',('"'+(Join-Path $PSScriptRoot 'agent')+'"'),'--host','127.0.0.1','--port','8766') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $data 'agent.out.log') -RedirectStandardError (Join-Path $data 'agent.err.log') -PassThru
    $processes += @{pid=$p.Id;created=$p.StartTime.ToUniversalTime().ToString('o')}
    $p = Start-Process -FilePath (Get-Command java).Source -ArgumentList @('-jar',('"'+$jar+'"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $data 'java.out.log') -RedirectStandardError (Join-Path $data 'java.err.log') -PassThru
    $processes += @{pid=$p.Id;created=$p.StartTime.ToUniversalTime().ToString('o')}
    @{project=$PSScriptRoot;processes=$processes} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $control -Encoding utf8
    $ready = $false
    for ($attempt=0; $attempt -lt 90; $attempt++) {
        try { $h=Invoke-RestMethod 'http://127.0.0.1:8765/api/health' -TimeoutSec 2; if ($h.agent) { $ready=$true; break } } catch {}
        Start-Sleep -Milliseconds 700
    }
    if (-not $ready) { throw 'Services did not become ready. See runtime/*.log and run stop.ps1 before restarting.' }
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8765' }
    Write-Output 'Ready: http://127.0.0.1:8765'
} catch {
    Write-Error $_
    throw
}
