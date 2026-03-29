param(
  [string]$Name = "playwright",
  [string]$Browser = "msedge",
  [switch]$Headless,
  [switch]$Isolated,
  [string]$OutputDir = "",
  [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\mcp-config.ps1")

$root = Get-ProjectRoot
$ConfigPath = Get-McpConfigPath -ConfigPath $ConfigPath

if (-not $OutputDir) {
  $OutputDir = Join-Path $root "data\playwright-mcp"
}

if (-not (Test-Path $OutputDir)) {
  New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

$config = Load-McpConfig -ConfigPath $ConfigPath

$args = @(
  "-y",
  "@playwright/mcp@latest",
  "--browser",
  $Browser,
  "--output-dir",
  (Resolve-Path $OutputDir).Path
)

if ($Headless) {
  $args += "--headless"
}

if ($Isolated) {
  $args += "--isolated"
}

$config["mcpServers"][$Name] = [ordered]@{
  transport = "stdio"
  command = "npx"
  args = $args
}

Save-McpConfig -ConfigPath $ConfigPath -Config $config

Write-Host "Updated $ConfigPath"
Write-Host "Browser MCP profile '$Name' is ready."
Write-Host "  Browser: $Browser"
Write-Host "  Headless: $($Headless.IsPresent)"
Write-Host "  Isolated: $($Isolated.IsPresent)"
Write-Host "  OutputDir: $((Resolve-Path $OutputDir).Path)"
