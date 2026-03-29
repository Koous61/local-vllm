param(
  [string]$BaseUrl = "http://localhost:8000",
  [switch]$NoHttpChecks
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\common.ps1")

function Test-CommandAvailable {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-CommandVersion {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Command,

    [string[]]$Arguments = @("--version")
  )

  try {
    $output = & $Command @Arguments 2>$null
    if ($LASTEXITCODE -eq 0 -and $output) {
      return (($output | Select-Object -First 1).ToString().Trim())
    }
  } catch {
  }

  return $null
}

function Write-Check {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    [Parameter(Mandatory = $true)]
    [bool]$Ok,

    [string]$Details = ""
  )

  $status = if ($Ok) { "OK" } else { "FAIL" }
  if ($Details) {
    Write-Host ("[{0}] {1}: {2}" -f $status, $Name, $Details)
  } else {
    Write-Host ("[{0}] {1}" -f $status, $Name)
  }
}

$root = Get-ProjectRoot
$envFile = Ensure-EnvFile
$envValues = Read-DotEnv -Path $envFile
$apiKey = if ($envValues.ContainsKey("API_KEY") -and $envValues["API_KEY"]) {
  $envValues["API_KEY"]
} else {
  "local-vllm-key"
}
$openWebUiPort = if ($envValues.ContainsKey("OPEN_WEBUI_PORT") -and $envValues["OPEN_WEBUI_PORT"]) {
  $envValues["OPEN_WEBUI_PORT"]
} else {
  "3010"
}
$mcpConfigPath = Join-Path $root "mcp-servers.json"
$composeArgs = Get-ComposeArgs

Write-Host "Project root: $root"
Write-Host "Environment file: $envFile"
Write-Host ""

$dockerOk = Test-CommandAvailable "docker"
$pythonOk = Test-CommandAvailable "python"
$nodeOk = Test-CommandAvailable "node"
$npxOk = Test-CommandAvailable "npx"

Write-Check -Name "docker command" -Ok $dockerOk -Details (Get-CommandVersion -Command "docker")
Write-Check -Name "python command" -Ok $pythonOk -Details (Get-CommandVersion -Command "python")
Write-Check -Name "node command" -Ok $nodeOk -Details (Get-CommandVersion -Command "node")
Write-Check -Name "npx command" -Ok $npxOk -Details (Get-CommandVersion -Command "npx")

if (-not $dockerOk) {
  throw "Docker is required for this project."
}

$dockerEngineOk = $false
try {
  & docker info *> $null
  $dockerEngineOk = ($LASTEXITCODE -eq 0)
} catch {
  $dockerEngineOk = $false
}
Write-Check -Name "docker engine" -Ok $dockerEngineOk -Details ($(if ($dockerEngineOk) { "reachable" } else { "not reachable" }))

$servicesOk = $false
$serviceDetails = "stack not inspected"
if ($dockerEngineOk) {
  try {
    $services = & docker compose $composeArgs ps --format json 2>$null | ConvertFrom-Json
    if ($LASTEXITCODE -eq 0 -and $services) {
      $servicesOk = $true
      $serviceDetails = (($services | ForEach-Object { "{0}={1}" -f $_.Service, $_.State }) -join ", ")
    } else {
      $serviceDetails = "no compose services reported"
    }
  } catch {
    $serviceDetails = "compose status unavailable"
  }
}
Write-Check -Name "docker compose services" -Ok $servicesOk -Details $serviceDetails

$modelId = if ($envValues.ContainsKey("MODEL_ID")) { $envValues["MODEL_ID"] } else { "" }
$servedModelName = if ($envValues.ContainsKey("SERVED_MODEL_NAME")) { $envValues["SERVED_MODEL_NAME"] } else { "" }
$maxModelLen = if ($envValues.ContainsKey("MAX_MODEL_LEN")) { $envValues["MAX_MODEL_LEN"] } else { "" }

Write-Host ""
Write-Host "Configured model:"
Write-Host "  MODEL_ID=$modelId"
Write-Host "  SERVED_MODEL_NAME=$servedModelName"
Write-Host "  MAX_MODEL_LEN=$maxModelLen"

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
Write-Check -Name "local MCP virtualenv" -Ok (Test-Path $venvPython) -Details ($(if (Test-Path $venvPython) { $venvPython } else { "not created yet" }))

$mcpConfigOk = Test-Path $mcpConfigPath
$mcpDetails = "not configured"
if ($mcpConfigOk) {
  try {
    $mcpConfig = Get-Content -Path $mcpConfigPath -Raw | ConvertFrom-Json
    $serverNames = @()
    foreach ($property in $mcpConfig.mcpServers.PSObject.Properties) {
      $enabled = $true
      if ($null -ne $property.Value.PSObject.Properties["enabled"]) {
        if ($property.Value.enabled -is [string]) {
          $enabled = $property.Value.enabled.Trim().ToLowerInvariant() -notin @("false", "0", "no", "off")
        } else {
          $enabled = [bool]$property.Value.enabled
        }
      }
      $status = if ($enabled) { "enabled" } else { "disabled" }
      $serverNames += ("{0}={1}" -f $property.Name, $status)
    }
    $mcpDetails = if ($serverNames.Count -gt 0) { $serverNames -join ", " } else { "no servers" }
  } catch {
    $mcpConfigOk = $false
    $mcpDetails = "invalid JSON"
  }
}
Write-Check -Name "MCP config" -Ok $mcpConfigOk -Details $mcpDetails

if ($NoHttpChecks) {
  Write-Host ""
  Write-Host "HTTP checks skipped because -NoHttpChecks was used."
  exit 0
}

Write-Host ""
$vllmOk = $false
$vllmDetails = "not reachable"
try {
  $health = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 10
  if ($health.StatusCode -eq 200) {
    $headers = @{ Authorization = "Bearer $apiKey" }
    $models = Invoke-RestMethod -Uri "$BaseUrl/v1/models" -Headers $headers -TimeoutSec 15
    $modelNames = @($models.data | ForEach-Object { $_.id })
    $vllmOk = $true
    $vllmDetails = if ($modelNames.Count -gt 0) { $modelNames -join ", " } else { "no models reported" }
  }
} catch {
  $vllmDetails = $_.Exception.Message
}
Write-Check -Name "vLLM HTTP API" -Ok $vllmOk -Details $vllmDetails

$webUiUrl = "http://localhost:$openWebUiPort"
$webUiOk = $false
$webUiDetails = "not reachable"
try {
  $ui = Invoke-WebRequest -Uri $webUiUrl -UseBasicParsing -TimeoutSec 10
  if ($ui.StatusCode -ge 200 -and $ui.StatusCode -lt 400) {
    $webUiOk = $true
    $webUiDetails = "HTTP $($ui.StatusCode)"
  }
} catch {
  $webUiDetails = $_.Exception.Message
}
Write-Check -Name "Open WebUI" -Ok $webUiOk -Details $webUiDetails
