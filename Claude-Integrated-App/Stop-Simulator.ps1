[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8505
)

$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath(
    (Split-Path -Parent $MyInvocation.MyCommand.Path)
)
$simulatorRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot "simulator")
)
$appPath = [System.IO.Path]::GetFullPath(
    (Join-Path $simulatorRoot "app.py")
)
$pidFile = Join-Path $simulatorRoot ".streamlit-simulator.pid"

function Test-ThisSimulatorCommand {
    param([AllowNull()][string]$CommandLine)

    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }

    return (
        $CommandLine.IndexOf(
            $appPath,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -ge 0 -and
        $CommandLine.IndexOf(
            "streamlit",
            [System.StringComparison]::OrdinalIgnoreCase
        ) -ge 0
    )
}

$matchingProcesses = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            Test-ThisSimulatorCommand $_.CommandLine
        }
)

$stoppedProcessIds = @()
foreach ($record in ($matchingProcesses | Sort-Object ProcessId -Descending)) {
    $processId = [int]$record.ProcessId
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    $stoppedProcessIds += $processId
}

if ($stoppedProcessIds.Count -gt 0) {
    Start-Sleep -Milliseconds 800
}

Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue

$remainingListener = @(
    Get-NetTCPConnection `
        -State Listen `
        -LocalPort $Port `
        -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
)

if ($remainingListener.Count -gt 0) {
    Write-Warning (
        "This simulator is stopped, but port $Port is still used by PID(s): " +
        ($remainingListener -join ", ")
    )
}

if ($stoppedProcessIds.Count -eq 0) {
    Write-Host "The simulator was already stopped."
}
else {
    Write-Host (
        "Stopped simulator process(es): " +
        ($stoppedProcessIds -join ", ")
    ) -ForegroundColor Green
}
