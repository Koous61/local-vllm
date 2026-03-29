param(
  [string[]]$ProjectPath = @(),
  [string]$Name = "node",
  [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\common.ps1")
. (Join-Path $PSScriptRoot "..\lib\mcp-config.ps1")

function Get-DetectedProjectPath {
  param(
    [string]$BasePath
  )

  $packageJson = Join-Path $BasePath "package.json"
  if (Test-Path $packageJson) {
    return @($BasePath)
  }

  return @()
}

$root = Get-ProjectRoot
$ConfigPath = Get-McpConfigPath -ConfigPath $ConfigPath

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  throw "The 'node' command was not found. Install Node.js first."
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  Write-Host "Local MCP virtualenv not found yet. Running setup-mcp first."
  & (Join-Path $PSScriptRoot "setup-mcp.ps1")
}

if (-not (Test-Path $venvPython)) {
  throw "Could not find $venvPython. Run .\setup-mcp.cmd first."
}

if (-not $ProjectPath -or $ProjectPath.Count -eq 0) {
  $ProjectPath = Get-DetectedProjectPath -BasePath $root
}

if (-not $ProjectPath -or $ProjectPath.Count -eq 0) {
  throw "No Node.js projects were detected from '$root'. Pass -ProjectPath explicitly."
}

$resolvedRoots = @()
foreach ($path in $ProjectPath) {
  $resolvedRoots += (Resolve-Path $path).Path
}
$resolvedRoots = $resolvedRoots | Sort-Object -Unique

$config = Load-McpConfig -ConfigPath $ConfigPath
$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot "node-mcp.py")).Path

$args = @($scriptPath)
foreach ($path in $resolvedRoots) {
  $args += "--project-root"
  $args += $path
}

$config["mcpServers"][$Name] = [ordered]@{
  enabled = $true
  transport = "stdio"
  command = $venvPython
  args = $args
}

Save-McpConfig -ConfigPath $ConfigPath -Config $config

Write-Host "Updated $ConfigPath"
Write-Host "Node MCP profile '$Name' is ready."
Write-Host "  Python: $venvPython"
foreach ($path in $resolvedRoots) {
  Write-Host "  Project: $path"
}
