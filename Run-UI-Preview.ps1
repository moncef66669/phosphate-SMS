[CmdletBinding()]
param(
    # Deliberately different from the production launcher's 8505 so the two
    # apps can run side by side for comparison.
    [ValidateRange(1024, 65535)]
    [int]$Port = 8606,

    [ValidateSet("flow", "situation", "schedule", "coverage")]
    [string]$Page = "flow",

    [ValidateSet("s1", "s2", "s3")]
    [string]$Scenario = "s3",

    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath(
    (Split-Path -Parent $MyInvocation.MyCommand.Path)
)
$previewRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot "simulator_preview")
)
$appPath = [System.IO.Path]::GetFullPath(
    (Join-Path $previewRoot "app_preview.py")
)
$runtimeRoot = Join-Path $projectRoot "generated_outputs\runtime"

if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
    throw "Preview entry point not found: $appPath"
}

# Ownership guard: match on THIS app's path only, so this launcher can never
# stop the production simulator (app.py) and vice versa.
function Test-ThisPreviewCommand {
    param([AllowNull()][string]$CommandLine)

    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }

    return (
        $CommandLine.IndexOf(
            $appPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $CommandLine.IndexOf(
            "streamlit", [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    )
}

$old = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { Test-ThisPreviewCommand $_.CommandLine }
)
foreach ($record in ($old | Sort-Object ProcessId -Descending)) {
    Stop-Process -Id ([int]$record.ProcessId) -Force -ErrorAction SilentlyContinue
}
if ($old.Count -gt 0) { Start-Sleep -Milliseconds 700 }

$listeners = @(
    Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
)
foreach ($listenerProcessId in $listeners) {
    $record = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $listenerProcessId" -ErrorAction SilentlyContinue

    if ($null -ne $record -and (Test-ThisPreviewCommand $record.CommandLine)) {
        Stop-Process -Id $listenerProcessId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 400
    }
    else {
        $owner = if ($null -ne $record) {
            "$($record.Name) (PID $listenerProcessId)"
        } else { "PID $listenerProcessId" }
        throw "Port $Port is already occupied by $owner. Stop it or pass -Port."
    }
}

$pythonCandidates = @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot "simulator\.venv\Scripts\python.exe")
)
$pythonExecutable = $pythonCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1

if ([string]::IsNullOrWhiteSpace($pythonExecutable)) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $pythonCommand) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue |
            Select-Object -First 1
    }
    if ($null -eq $pythonCommand) {
        throw "Python was not found. Install Python or create .venv in the project root."
    }
    $pythonExecutable = $pythonCommand.Source
}

# Never write bytecode: importing simulator\ui\* from this process must not
# drop __pycache__ into the production tree.
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUNBUFFERED = "1"

& $pythonExecutable -c "import streamlit" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw (
        "Streamlit is not installed for `"$pythonExecutable`". Run: " +
        "python -m pip install -r `"$projectRoot\simulator\requirements.txt`""
    )
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$stdoutLog = Join-Path $runtimeRoot "ui-preview-$Port.stdout.log"
$stderrLog = Join-Path $runtimeRoot "ui-preview-$Port.stderr.log"

$streamlitArguments = @(
    "-m", "streamlit", "run", ('"{0}"' -f $appPath),
    "--server.address", "127.0.0.1",
    "--server.port", $Port.ToString(),
    "--server.headless", "true",
    "--browser.gatherUsageStats", "false",
    "--server.fileWatcherType", "poll"
)

$serverProcess = Start-Process `
    -FilePath $pythonExecutable `
    -ArgumentList $streamlitArguments `
    -WorkingDirectory $previewRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

$healthUrl = "http://127.0.0.1:$Port/_stcore/health"
$appUrl = "http://localhost:$Port/?page=$Page&scn=$Scenario"
$deadline = (Get-Date).AddSeconds(60)
$healthy = $false

do {
    Start-Sleep -Milliseconds 500
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) { break }
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing `
            -TimeoutSec 2 -ErrorAction Stop
        if ($response.StatusCode -eq 200) { $healthy = $true }
    }
    catch { }
} while (-not $healthy -and (Get-Date) -lt $deadline)

if (-not $healthy) {
    Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
    $errorTail = if (Test-Path -LiteralPath $stderrLog -PathType Leaf) {
        (Get-Content -LiteralPath $stderrLog -Tail 30) -join [Environment]::NewLine
    } else { "No error log was created." }
    throw "UI preview did not become healthy. Error log:`n$errorTail"
}

Write-Host ""
Write-Host "UI design preview is running (review only)." -ForegroundColor Green
Write-Host "Source : $appPath"
Write-Host "Python : $pythonExecutable"
Write-Host "URL    : $appUrl"
Write-Host "PID    : $($serverProcess.Id)"
Write-Host ""
Write-Host "The production simulator is untouched; launch it separately with"
Write-Host "RUN-LATEST-SIMULATOR.cmd (port 8505) to compare side by side."

if ($OpenBrowser) { Start-Process $appUrl }
