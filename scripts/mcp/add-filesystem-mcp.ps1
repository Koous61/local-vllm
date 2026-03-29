param(
  [string[]]$AllowedPath = @(),
  [string]$Name = "filesystem",
  [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\mcp-config.ps1")

$root = Get-ProjectRoot
$ConfigPath = Get-McpConfigPath -ConfigPath $ConfigPath

if (-not $AllowedPath -or $AllowedPath.Count -eq 0) {
  $AllowedPath = @($root)
}

$resolvedPaths = @()
foreach ($path in $AllowedPath) {
  $resolvedPaths += (Resolve-Path $path).Path
}

$config = Load-McpConfig -ConfigPath $ConfigPath

$args = @("-y", "@modelcontextprotocol/server-filesystem") + $resolvedPaths
$config["mcpServers"][$Name] = [ordered]@{
  transport = "stdio"
  command = "npx"
  args = $args
}

Save-McpConfig -ConfigPath $ConfigPath -Config $config

Write-Host "Updated $ConfigPath"
Write-Host "Filesystem MCP allowed paths:"
foreach ($path in $resolvedPaths) {
  Write-Host "  - $path"
}
