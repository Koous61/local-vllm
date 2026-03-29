param(
  [string[]]$ProjectPath = @(),
  [string]$Name = "docker",
  [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\common.ps1")
. (Join-Path $PSScriptRoot "..\lib\mcp-config.ps1")

function Get-DetectedProjectPath {
  param(
    [string]$BasePath
  )

  foreach ($fileName in @("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")) {
    if (Test-Path (Join-Path $BasePath $fileName)) {
      return @($BasePath)
    }
  }

  return @()
}

$root = Get-ProjectRoot
$ConfigPath = Get-McpConfigPath -ConfigPath $ConfigPath

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "The 'docker' command was not found. Install Docker Desktop first."
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
  throw "No Docker Compose projects were detected. Pass -ProjectPath explicitly."
}

$resolvedRoots = @()
foreach ($path in $ProjectPath) {
  $resolved = (Resolve-Path $path).Path
  $composeFound = $false
  foreach ($fileName in @("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")) {
    if (Test-Path (Join-Path $resolved $fileName)) {
      $composeFound = $true
      break
    }
  }

  if (-not $composeFound) {
    throw "No compose file was found in '$resolved'."
  }

  $resolvedRoots += $resolved
}
$resolvedRoots = $resolvedRoots | Sort-Object -Unique

$config = Load-McpConfig -ConfigPath $ConfigPath
$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot "docker-mcp.py")).Path

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
Write-Host "Docker MCP profile '$Name' is ready."
Write-Host "  Python: $venvPython"
foreach ($path in $resolvedRoots) {
  Write-Host "  Project: $path"
}
