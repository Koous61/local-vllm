param(
  [string]$BaseUrl = "http://localhost:8000",
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\common.ps1")

$envValues = Read-DotEnv -Path (Ensure-EnvFile)
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

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$vllmReady = $false
while ((Get-Date) -lt $deadline) {
  try {
    $health = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 10
    if ($health.StatusCode -eq 200) {
      $headers = @{ Authorization = "Bearer $apiKey" }
      $models = Invoke-RestMethod -Uri "$BaseUrl/v1/models" -Headers $headers -TimeoutSec 15
      $servedModels = @($models.data | ForEach-Object { $_.id }) -join ", "
      Write-Host "vLLM is ready at $BaseUrl"
      Write-Host "Available models: $servedModels"
      $vllmReady = $true
      break
    }
  } catch {
    Start-Sleep -Seconds 5
  }
}

if (-not $vllmReady) {
  throw "Timed out waiting for vLLM at $BaseUrl after $TimeoutSeconds seconds."
}

$uiUrl = "http://localhost:$openWebUiPort"
while ((Get-Date) -lt $deadline) {
  try {
    $ui = Invoke-WebRequest -Uri $uiUrl -UseBasicParsing -TimeoutSec 10
    if ($ui.StatusCode -ge 200 -and $ui.StatusCode -lt 400) {
      Write-Host "Open WebUI is ready at $uiUrl"
      exit 0
    }
  } catch {
    Start-Sleep -Seconds 3
  }
}

throw "Timed out waiting for Open WebUI at $uiUrl after $TimeoutSeconds seconds."
