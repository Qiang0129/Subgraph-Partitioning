$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$control = Join-Path $PSScriptRoot 'runtime\processes.json'
if (-not (Test-Path -LiteralPath $control)) { Write-Output 'No recorded processes.'; return }
$state = Get-Content -LiteralPath $control -Raw -Encoding utf8 | ConvertFrom-Json
if ([IO.Path]::GetFullPath($state.project) -ne [IO.Path]::GetFullPath($PSScriptRoot)) { throw 'Process record belongs to another project.' }
$snapshot = @(Get-CimInstance Win32_Process)
$targets = @{}
foreach ($entry in $state.processes) {
    $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    $command = (Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.pid)").CommandLine
    $recordedTime = ([datetime]$entry.created).ToUniversalTime()
    if ($process.StartTime.ToUniversalTime().Ticks -ne $recordedTime.Ticks -or -not $command -or -not $command.Contains($PSScriptRoot)) { throw "PID $($entry.pid) does not match this project's recorded process; refusing to stop it." }
    $targets[[int]$entry.pid] = $process.StartTime.ToUniversalTime().ToString('o')
    $parents = @([int]$entry.pid)
    while ($parents.Count -gt 0) {
        $children = @($snapshot | Where-Object { [int]$_.ParentProcessId -in $parents -and -not $targets.ContainsKey([int]$_.ProcessId) })
        $parents = @()
        foreach ($child in $children) {
            # Console hosts exit with their attached services; they carry no
            # project command line and are not application processes to stop.
            if ($child.Name -eq 'conhost.exe' -and $child.ExecutablePath -eq (Join-Path $env:WINDIR 'System32\conhost.exe')) { continue }
            if (-not $child.CommandLine -or -not $child.CommandLine.Contains($PSScriptRoot) -or $child.CreationDate -lt $process.StartTime) {
                throw "Child PID $($child.ProcessId) does not match this project; refusing to stop it."
            }
            $childProcess = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            if (-not $childProcess) { continue }
            if ([Math]::Abs(($childProcess.StartTime - $child.CreationDate).TotalMilliseconds) -gt 1) { throw 'Child PID changed identity.' }
            $targets[[int]$child.ProcessId] = $childProcess.StartTime.ToUniversalTime().ToString('o')
            $parents += [int]$child.ProcessId
        }
    }
}
# Validate the whole tree before stopping anything. Python venv launchers and
# Java shims can each leave a separate child listening on the service port.
$toStop = @()
foreach ($targetId in $targets.Keys) {
    $process = Get-Process -Id $targetId -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    $command = (Get-CimInstance Win32_Process -Filter "ProcessId=$targetId").CommandLine
    if ($process.StartTime.ToUniversalTime().ToString('o') -ne $targets[$targetId] -or -not $command -or -not $command.Contains($PSScriptRoot)) {
        throw "PID $targetId changed identity; refusing to stop it."
    }
    $toStop += $process
}
foreach ($process in ($toStop | Sort-Object StartTime -Descending)) {
    if ($process.HasExited) { continue }
    try { Stop-Process -InputObject $process -ErrorAction Stop }
    catch { if (-not $process.HasExited) { throw } }
}
Remove-Item -LiteralPath $control
Write-Output 'Stopped this project only.'
