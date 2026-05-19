param(
  [string]$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$Assets = "all",
  [int]$IntervalSeconds = 10,
  [int]$RestartDelaySeconds = 10,
  [int]$RetryAttempts = 5,
  [double]$RetryInitialDelaySeconds = 1,
  [double]$RetryMaxDelaySeconds = 30
)

$ErrorActionPreference = "Stop"

$apiDir = Join-Path $ProjectRoot "services\api"
$pythonExe = Join-Path $apiDir ".venv\Scripts\python.exe"
$logDir = Join-Path $apiDir "logs"

if (-not (Test-Path -LiteralPath $apiDir)) {
  throw "API directory not found: $apiDir"
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
  throw "Python venv not found: $pythonExe"
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Set-Location -LiteralPath $apiDir

$date = Get-Date -Format "yyyy-MM-dd"
$logFile = Join-Path $logDir "chainlink-worker-$date.log"
$mutexName = "Local\PolyCryptoChainlinkWorkerWatchdog"
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$hasMutex = $false

try {
  $hasMutex = $mutex.WaitOne(0)
  if (-not $hasMutex) {
    Add-Content -LiteralPath $logFile -Encoding UTF8 -Value "[$(Get-Date -Format "o")] Chainlink worker watchdog is already running; exiting duplicate launcher."
    exit 0
  }

  while ($true) {
  $date = Get-Date -Format "yyyy-MM-dd"
  $logFile = Join-Path $logDir "chainlink-worker-$date.log"
  $startedAt = Get-Date -Format "o"
  Add-Content -LiteralPath $logFile -Encoding UTF8 -Value "[$startedAt] starting Chainlink worker"

  try {
    $commandLine = @(
      "`"$pythonExe`"",
      "-m app.scripts.poll_chainlink_streams",
      "--asset `"$Assets`"",
      "--interval $IntervalSeconds",
      "--retry-attempts $RetryAttempts",
      "--retry-initial-delay $RetryInitialDelaySeconds",
      "--retry-max-delay $RetryMaxDelaySeconds",
      ">> `"$logFile`" 2>&1"
    ) -join " "

    & cmd.exe /d /c $commandLine
    $exitCode = $LASTEXITCODE
  } catch {
    $exitCode = 1
    Add-Content -LiteralPath $logFile -Encoding UTF8 -Value "[$(Get-Date -Format "o")] watchdog caught error: $($_.Exception.Message)"
  }

  $stoppedAt = Get-Date -Format "o"
  Add-Content -LiteralPath $logFile -Encoding UTF8 -Value "[$stoppedAt] worker exited with code $exitCode; restarting in $RestartDelaySeconds seconds"
  Start-Sleep -Seconds $RestartDelaySeconds
  }
} finally {
  if ($hasMutex) {
    $mutex.ReleaseMutex()
  }
  $mutex.Dispose()
}
