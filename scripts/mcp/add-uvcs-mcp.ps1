param(
  [string[]]$WorkspacePath = @(),
  [string]$Name = "uvcs",
  [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\common.ps1")
. (Join-Path $PSScriptRoot "..\lib\mcp-config.ps1")

function Get-DetectedWorkspacePath {
  $paths = @()
  $lines = & cm workspace list
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to enumerate UVCS workspaces via 'cm workspace list'."
  }

  foreach ($line in $lines) {
    if ($line -match '^\S+\s+(.+)$') {
      $paths += $matches[1].Trim()
    }
  }

  return $paths
}

$root = Get-ProjectRoot
$ConfigPath = Get-McpConfigPath -ConfigPath $ConfigPath

if (-not (Get-Command cm -ErrorAction SilentlyContinue)) {
  throw "The 'cm' command was not found. Install the UVCS / Plastic SCM client first."
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  Write-Host "Local MCP virtualenv not found yet. Running setup-mcp first."
  & (Join-Path $PSScriptRoot "setup-mcp.ps1")
}

if (-not (Test-Path $venvPython)) {
  throw "Could not find $venvPython. Run .\setup-mcp.cmd first."
}

if (-not $WorkspacePath -or $WorkspacePath.Count -eq 0) {
  $WorkspacePath = Get-DetectedWorkspacePath
}

if (-not $WorkspacePath -or $WorkspacePath.Count -eq 0) {
  throw "No UVCS workspaces were detected. Pass -WorkspacePath explicitly."
}

$resolvedRoots = @()
foreach ($path in $WorkspacePath) {
  $resolvedRoots += (Resolve-Path $path).Path
}
$resolvedRoots = $resolvedRoots | Sort-Object -Unique

$config = Load-McpConfig -ConfigPath $ConfigPath
$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot "uvcs-mcp.py")).Path

$args = @($scriptPath)
foreach ($path in $resolvedRoots) {
  $args += "--workspace-root"
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
Write-Host "UVCS MCP profile '$Name' is ready."
Write-Host "  Python: $venvPython"
foreach ($path in $resolvedRoots) {
  Write-Host "  Workspace: $path"
}
