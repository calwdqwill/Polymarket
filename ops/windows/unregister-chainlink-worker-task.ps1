param(
  [string]$TaskName = "PolyCrypto Chainlink Worker"
)

$ErrorActionPreference = "Stop"

$startupLauncher = Join-Path ([Environment]::GetFolderPath("Startup")) "PolyCryptoChainlinkWorker.cmd"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Output "Unregistered scheduled task: $TaskName"
} else {
  Write-Output "Scheduled task not found: $TaskName"
}

if (Test-Path -LiteralPath $startupLauncher) {
  Remove-Item -LiteralPath $startupLauncher -Force
  Write-Output "Removed Startup launcher: $startupLauncher"
} else {
  Write-Output "Startup launcher not found: $startupLauncher"
}
