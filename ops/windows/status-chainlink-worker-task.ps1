param(
  [string]$TaskName = "PolyCrypto Chainlink Worker",
  [string]$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"

$startupLauncher = Join-Path ([Environment]::GetFolderPath("Startup")) "PolyCryptoChainlinkWorker.cmd"
$apiDir = Join-Path $ProjectRoot "services\api"
$logDir = Join-Path $apiDir "logs"
$latestLog = Get-ChildItem -LiteralPath $logDir -Filter "chainlink-worker-*.log" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
  [pscustomobject]@{
    Mode = "StartupLauncher"
    TaskName = $TaskName
    ScheduledTaskState = "NotRegistered"
    StartupLauncherExists = Test-Path -LiteralPath $startupLauncher
    StartupLauncher = $startupLauncher
    LatestLog = $latestLog.FullName
    LatestLogUpdatedAt = $latestLog.LastWriteTime
  }
  exit 0
}

$info = Get-ScheduledTaskInfo -TaskName $TaskName

[pscustomobject]@{
  Mode = "ScheduledTask"
  TaskName = $task.TaskName
  State = $task.State
  LastRunTime = $info.LastRunTime
  LastTaskResult = $info.LastTaskResult
  NextRunTime = $info.NextRunTime
  StartupLauncherExists = Test-Path -LiteralPath $startupLauncher
  StartupLauncher = $startupLauncher
  LatestLog = $latestLog.FullName
  LatestLogUpdatedAt = $latestLog.LastWriteTime
}
