$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$composeArgs = Get-ComposeArgs
& docker compose $composeArgs down
exit $LASTEXITCODE
