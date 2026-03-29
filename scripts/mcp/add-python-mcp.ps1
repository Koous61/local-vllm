param(
  [string]$ProjectPath = "",
  [string]$Name = "python",
  [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\common.ps1")
. (Join-Path $PSScriptRoot "..\lib\mcp-config.ps1")

function Get-DetectedProjectPath {
  param(
    [string]$BasePath
  )

  $markers = @(
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "tox.ini"
  )

  foreach ($marker in $markers) {
    if (Test-Path (Join-Path $BasePath $marker)) {
      return $BasePath
    }
  }

  $pythonFiles = Get-ChildItem -Path $BasePath -Recurse -Filter *.py -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch "\\\.venv\\|\\venv\\|\\site-packages\\|\\node_modules\\|\\data\\" } |
    Select-Object -First 1

  if ($pythonFiles) {
    return $BasePath
  }

  return ""
}

$root = Get-ProjectRoot
$ConfigPath = Get-McpConfigPath -ConfigPath $ConfigPath

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  Write-Host "Local MCP virtualenv not found yet. Running setup-mcp first."
  & (Join-Path $PSScriptRoot "setup-mcp.ps1")
}

if (-not (Test-Path $venvPython)) {
  throw "Could not find $venvPython. Run .\setup-mcp.cmd first."
}

if (-not $ProjectPath) {
  $ProjectPath = Get-DetectedProjectPath -BasePath $root
}

if (-not $ProjectPath) {
  throw "No Python project was detected from '$root'. Pass -ProjectPath explicitly."
}

$resolvedRoot = (Resolve-Path $ProjectPath).Path
$config = Load-McpConfig -ConfigPath $ConfigPath
$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot "python-mcp.py")).Path

$config["mcpServers"][$Name] = [ordered]@{
  enabled = $true
  transport = "stdio"
  command = $venvPython
  args = @(
    $scriptPath,
    "--project-root",
    $resolvedRoot
  )
}

Save-McpConfig -ConfigPath $ConfigPath -Config $config

Write-Host "Updated $ConfigPath"
Write-Host "Python MCP profile '$Name' is ready."
Write-Host "  Python: $venvPython"
Write-Host "  Project: $resolvedRoot"
