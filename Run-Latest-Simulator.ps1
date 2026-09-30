[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8505,

    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath(
    (Split-Path -Parent $MyInvocation.MyCommand.Path)
)
$simulatorRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot "Claude-Integrated-App\simulator")
)
$appPath = [System.IO.Path]::GetFullPath(
    (Join-Path $simulatorRoot "app.py")
)
$pidFile = Join-Path $simulatorRoot ".streamlit-simulator.pid"
$runtimeRoot = Join-Path $projectRoot "generated_outputs\runtime"

if (-not (Test-Path -LiteralPath $appPath -PathType Leaf)) {
    throw "Simulator entry point not found: $appPath"
}

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

function Stop-ThisSimulatorProcess {
    param([int]$ProcessId)

    $record = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $ProcessId" `
        -ErrorAction SilentlyContinue

    if ($null -ne $record -and (Test-ThisSimulatorCommand $record.CommandLine)) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        return $true
    }

    return $false
}

# Stop only prior copies of this exact simulator entry point. Other Streamlit
# dashboards are deliberately left alone.
$oldProcesses = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            Test-ThisSimulatorCommand $_.CommandLine
        }
)

$stoppedProcessIds = @()
foreach ($record in ($oldProcesses | Sort-Object ProcessId -Descending)) {
    $processId = [int]$record.ProcessId
    if (Stop-ThisSimulatorProcess $processId) {
        $stoppedProcessIds += $processId
    }
}

if ($stoppedProcessIds.Count -gt 0) {
    Start-Sleep -Milliseconds 800
}

# Refuse to replace an unrelated service that happens to use the requested port.
$listeners = @(
    Get-NetTCPConnection `
        -State Listen `
        -LocalPort $Port `
        -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
)

foreach ($listenerProcessId in $listeners) {
    $record = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $listenerProcessId" `
        -ErrorAction SilentlyContinue

    if ($null -ne $record -and (Test-ThisSimulatorCommand $record.CommandLine)) {
        Stop-Process -Id $listenerProcessId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
    else {
        $owner = if ($null -ne $record) {
            "$($record.Name) (PID $listenerProcessId)"
        }
        else {
            "PID $listenerProcessId"
        }

        throw "Port $Port is already occupied by $owner. Stop it or choose another port."
    }
}

# Remove only disposable Python bytecode caches below simulator/. This prevents
# a warm Python process from presenting an older imported layout after an update.
$simulatorPrefix = $simulatorRoot.TrimEnd(
    [char[]]@("\", "/")
) + [System.IO.Path]::DirectorySeparatorChar

$cacheDirectories = @(
    Get-ChildItem `
        -LiteralPath $simulatorRoot `
        -Directory `
        -Filter "__pycache__" `
        -Recurse `
        -Force `
        -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } -Descending
)

foreach ($cacheDirectory in $cacheDirectories) {
    $cachePath = [System.IO.Path]::GetFullPath($cacheDirectory.FullName)
    $isInsideSimulator = $cachePath.StartsWith(
        $simulatorPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
    $isReparsePoint = (
        $cacheDirectory.Attributes -band
        [System.IO.FileAttributes]::ReparsePoint
    ) -ne 0

    if (-not $isInsideSimulator -or $isReparsePoint) {
        throw "Refusing to remove an unsafe cache path: $cachePath"
    }

    Remove-Item -LiteralPath $cachePath -Recurse -Force
}

$pythonCandidates = @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $simulatorRoot ".venv\Scripts\python.exe")
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

$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUNBUFFERED = "1"

& $pythonExecutable -c "import streamlit" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw (
        "Streamlit is not installed for `"$pythonExecutable`". Run: " +
        "python -m pip install -r `"$simulatorRoot\requirements.txt`""
    )
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$stdoutLog = Join-Path $runtimeRoot "streamlit-$Port.stdout.log"
$stderrLog = Join-Path $runtimeRoot "streamlit-$Port.stderr.log"

$streamlitArguments = @(
    "-m",
    "streamlit",
    "run",
    ('"{0}"' -f $appPath),
    "--server.address",
    "127.0.0.1",
    "--server.port",
    $Port.ToString(),
    "--server.headless",
    "true",
    "--browser.gatherUsageStats",
    "false",
    "--server.fileWatcherType",
    "poll",
    "--server.runOnSave",
    "true"
)

$serverProcess = Start-Process `
    -FilePath $pythonExecutable `
    -ArgumentList $streamlitArguments `
    -WorkingDirectory $simulatorRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

Set-Content `
    -LiteralPath $pidFile `
    -Value $serverProcess.Id `
    -Encoding Ascii

$healthUrl = "http://127.0.0.1:$Port/_stcore/health"
$launchToken = Get-Date -Format "yyyyMMddHHmmss"
$appUrl = "http://localhost:$Port/?page=15&scn=s3&rev=$launchToken"
$deadline = (Get-Date).AddSeconds(45)
$healthy = $false

do {
    Start-Sleep -Milliseconds 500
    $serverProcess.Refresh()

    if ($serverProcess.HasExited) {
        break
    }

    try {
        $response = Invoke-WebRequest `
            -Uri $healthUrl `
            -UseBasicParsing `
            -TimeoutSec 2 `
            -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            $healthy = $true
        }
    }
    catch {
        # The server is still starting.
    }
} while (-not $healthy -and (Get-Date) -lt $deadline)

if (-not $healthy) {
    Stop-ThisSimulatorProcess $serverProcess.Id | Out-Null
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue

    $errorTail = if (Test-Path -LiteralPath $stderrLog -PathType Leaf) {
        (Get-Content -LiteralPath $stderrLog -Tail 30) -join [Environment]::NewLine
    }
    else {
        "No error log was created."
    }

    throw "Simulator did not become healthy. Error log:`n$errorTail"
}

Write-Host ""
Write-Host "Latest simulator is running." -ForegroundColor Green
Write-Host "Source : $appPath"
Write-Host "Python : $pythonExecutable"
Write-Host "URL    : $appUrl"
Write-Host "PID    : $($serverProcess.Id)"
Write-Host "Caches : removed $($cacheDirectories.Count) __pycache__ folder(s)"
Write-Host ""
Write-Host "Code changes reload automatically while this process is running."
Write-Host "Run this launcher again only if the simulator has stopped."

if ($OpenBrowser) {
    Start-Process $appUrl
}
