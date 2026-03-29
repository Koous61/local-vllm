$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\common.ps1")

$composeArgs = Get-ComposeArgs
& docker compose $composeArgs down
exit $LASTEXITCODE
