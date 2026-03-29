param(
  [string[]]$RepoPath = @(),
  [string]$Name = "git",
  [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\common.ps1")
. (Join-Path $PSScriptRoot "..\lib\mcp-config.ps1")

function Get-DetectedRepoPath {
  param(
    [string]$BasePath
  )

  $output = & git -C $BasePath rev-parse --show-toplevel 2>$null
  if ($LASTEXITCODE -ne 0) {
    return @()
  }

  if (-not $output) {
    return @()
  }

  return @($output | Select-Object -First 1)
}

$root = Get-ProjectRoot
$ConfigPath = Get-McpConfigPath -ConfigPath $ConfigPath

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "The 'git' command was not found. Install Git first."
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  Write-Host "Local MCP virtualenv not found yet. Running setup-mcp first."
  & (Join-Path $PSScriptRoot "setup-mcp.ps1")
}

if (-not (Test-Path $venvPython)) {
  throw "Could not find $venvPython. Run .\setup-mcp.cmd first."
}

if (-not $RepoPath -or $RepoPath.Count -eq 0) {
  $RepoPath = Get-DetectedRepoPath -BasePath $root
}

if (-not $RepoPath -or $RepoPath.Count -eq 0) {
  throw "No Git repositories were detected. Pass -RepoPath explicitly."
}

$resolvedRoots = @()
foreach ($path in $RepoPath) {
  $resolvedRoots += (Resolve-Path $path).Path
}
$resolvedRoots = $resolvedRoots | Sort-Object -Unique

$config = Load-McpConfig -ConfigPath $ConfigPath
$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot "git-mcp.py")).Path

$args = @($scriptPath)
foreach ($path in $resolvedRoots) {
  $args += "--repo-root"
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
Write-Host "Git MCP profile '$Name' is ready."
Write-Host "  Python: $venvPython"
foreach ($path in $resolvedRoots) {
  Write-Host "  Repository: $path"
}
