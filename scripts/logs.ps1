param(
  [int]$Tail = 200
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$composeArgs = Get-ComposeArgs
& docker compose $composeArgs logs -f --tail=$Tail vllm
exit $LASTEXITCODE
