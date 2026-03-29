param(
  [string]$BaseUrl = "http://localhost:8000",
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$envValues = Read-DotEnv -Path (Ensure-EnvFile)
$apiKey = if ($envValues.ContainsKey("API_KEY") -and $envValues["API_KEY"]) {
  $envValues["API_KEY"]
} else {
  "local-vllm-key"
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
  try {
    $health = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing -TimeoutSec 10
    if ($health.StatusCode -eq 200) {
      $headers = @{ Authorization = "Bearer $apiKey" }
      $models = Invoke-RestMethod -Uri "$BaseUrl/v1/models" -Headers $headers -TimeoutSec 15
      $servedModels = @($models.data | ForEach-Object { $_.id }) -join ", "
      Write-Host "vLLM is ready at $BaseUrl"
      Write-Host "Available models: $servedModels"
      exit 0
    }
  } catch {
    Start-Sleep -Seconds 5
  }
}

throw "Timed out waiting for vLLM at $BaseUrl after $TimeoutSeconds seconds."
