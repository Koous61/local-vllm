param(
  [string[]]$AllowedPath = @()
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$root = Get-ProjectRoot
$venvDir = Join-Path $root ".venv"
$python = Join-Path $venvDir "Scripts\python.exe"

if (-not (Test-Path $python)) {
  Write-Host "Creating Python virtual environment in $venvDir"
  python -m venv $venvDir
}

Write-Host "Installing MCP client dependencies"
& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $root "requirements-mcp.txt")

if (-not $AllowedPath -or $AllowedPath.Count -eq 0) {
  $AllowedPath = @($root)
}

& (Join-Path $PSScriptRoot "add-filesystem-mcp.ps1") -AllowedPath $AllowedPath

Write-Host ""
Write-Host "MCP terminal client is ready."
Write-Host "Run .\mcp-chat.cmd --server filesystem"
Write-Host "Optional browser profile: .\add-browser-mcp.cmd"
