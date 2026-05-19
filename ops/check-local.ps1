param(
  [switch]$SkipFrontendBuild,
  [switch]$SkipApiHealth
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$ApiDir = Join-Path $ProjectRoot "services\api"
$WebDir = Join-Path $ProjectRoot "apps\web"
$PythonExe = Join-Path $ApiDir ".venv\Scripts\python.exe"

function Invoke-Step {
  param(
    [string]$Name,
    [scriptblock]$Command
  )

  Write-Output ""
  Write-Output "== $Name =="
  & $Command
}

function Invoke-Native {
  param(
    [string]$FilePath,
    [string[]]$Arguments
  )

  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
  }
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
  throw "Python venv not found: $PythonExe"
}

Invoke-Step "Backend compile" {
  Push-Location -LiteralPath $ApiDir
  try {
    Invoke-Native $PythonExe @("-m", "compileall", "app")
  } finally {
    Pop-Location
  }
}

Invoke-Step "Backend tests" {
  Push-Location -LiteralPath $ApiDir
  try {
    Invoke-Native $PythonExe @("-m", "unittest", "discover", "-s", "tests")
  } finally {
    Pop-Location
  }
}

Invoke-Step "Frontend lint" {
  Push-Location -LiteralPath $WebDir
  try {
    Invoke-Native "npm" @("run", "lint")
  } finally {
    Pop-Location
  }
}

Invoke-Step "Frontend typecheck" {
  Push-Location -LiteralPath $WebDir
  try {
    Invoke-Native "npm" @("run", "test")
  } finally {
    Pop-Location
  }
}

if (-not $SkipFrontendBuild) {
  Invoke-Step "Frontend build" {
    Push-Location -LiteralPath $WebDir
    try {
      Invoke-Native "npm" @("run", "build")
    } finally {
      Pop-Location
    }
  }
}

if (-not $SkipApiHealth) {
  Invoke-Step "Optional local API health" {
    try {
      $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/status/sources" -UseBasicParsing -TimeoutSec 3
      Write-Output "API status/sources HTTP $($response.StatusCode)"
    } catch {
      Write-Output "API is not running on http://127.0.0.1:8000; skipped live health check."
    }
  }
}

Write-Output ""
Write-Output "Local checks completed."
