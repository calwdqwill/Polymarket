param(
  [string]$TaskName = "PolyCrypto Chainlink Worker",
  [string]$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$Assets = "all",
  [int]$IntervalSeconds = 10
)

$ErrorActionPreference = "Stop"

$apiDir = Join-Path $ProjectRoot "services\api"
$watchdogScript = Join-Path $ProjectRoot "ops\windows\run-chainlink-worker.ps1"

if (-not (Test-Path -LiteralPath $watchdogScript)) {
  throw "Worker watchdog script not found: $watchdogScript"
}

if (-not (Test-Path -LiteralPath (Join-Path $apiDir ".env"))) {
  throw "services\api\.env not found. Configure Chainlink credentials before registering the worker task."
}

$startupDir = [Environment]::GetFolderPath("Startup")
$startupLauncher = Join-Path $startupDir "PolyCryptoChainlinkWorker.cmd"

$arguments = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "`"$watchdogScript`"",
  "-ProjectRoot", "`"$ProjectRoot`"",
  "-Assets", "`"$Assets`"",
  "-IntervalSeconds", "$IntervalSeconds"
) -join " "

function Start-WorkerWatchdog {
  Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList $arguments `
    -WorkingDirectory $apiDir `
    -WindowStyle Hidden
}

function Register-StartupLauncher {
  New-Item -ItemType Directory -Force -Path $startupDir | Out-Null
  $launcherContent = @"
@echo off
start "PolyCrypto Chainlink Worker" /min powershell.exe $arguments
"@
  Set-Content -LiteralPath $startupLauncher -Value $launcherContent -Encoding ASCII
  Start-WorkerWatchdog
  Write-Output "Task Scheduler registration was not allowed. Created Startup launcher instead: $startupLauncher"
  Write-Output "Started Chainlink worker watchdog in a hidden PowerShell process."
}

$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument $arguments `
  -WorkingDirectory $apiDir

$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)

try {
  Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Runs the Poly Crypto Chainlink Streams realtime worker watchdog at Windows logon." `
    -Force | Out-Null

  Start-ScheduledTask -TaskName $TaskName
  Write-Output "Registered and started scheduled task: $TaskName"
} catch {
  if ($_.Exception.Message -like "*Access is denied*") {
    Register-StartupLauncher
  } else {
    throw
  }
}
