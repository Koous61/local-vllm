param(
  [string]$BaseUrl = "http://localhost:8000",
  [string]$Prompt = "Reply with one short sentence saying the local vLLM server is ready."
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$envValues = Read-DotEnv -Path (Ensure-EnvFile)
$apiKey = if ($envValues.ContainsKey("API_KEY") -and $envValues["API_KEY"]) {
  $envValues["API_KEY"]
} else {
  "local-vllm-key"
}

$headers = @{
  Authorization = "Bearer $apiKey"
  "Content-Type" = "application/json"
}

$models = Invoke-RestMethod -Uri "$BaseUrl/v1/models" -Headers @{ Authorization = "Bearer $apiKey" } -TimeoutSec 30
if (-not $models.data -or $models.data.Count -eq 0) {
  throw "No models returned by $BaseUrl/v1/models"
}

$model = $models.data[0].id
$body = @{
  model = $model
  temperature = 0.2
  max_tokens = 120
  messages = @(
    @{
      role = "system"
      content = "You are a concise assistant."
    },
    @{
      role = "user"
      content = $Prompt
    }
  )
} | ConvertTo-Json -Depth 6

$response = Invoke-RestMethod -Uri "$BaseUrl/v1/chat/completions" -Method Post -Headers $headers -Body $body -TimeoutSec 120
$text = $response.choices[0].message.content

Write-Host "Model: $model"
Write-Host "Reply:"
Write-Host $text
