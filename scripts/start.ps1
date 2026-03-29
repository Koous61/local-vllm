param(
  [switch]$Follow,
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$root = Get-ProjectRoot
$composeArgs = Get-ComposeArgs

foreach ($dir in @(
  (Join-Path $root "data"),
  (Join-Path $root "data\\hf-cache"),
  (Join-Path $root "models")
)) {
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

& docker compose $composeArgs up -d
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

if ($Follow) {
  & docker compose $composeArgs logs -f --tail=200 vllm
  exit $LASTEXITCODE
}

& (Join-Path $PSScriptRoot "wait-ready.ps1") -TimeoutSeconds $TimeoutSeconds
exit $LASTEXITCODE
