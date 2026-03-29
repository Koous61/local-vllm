param(
  [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\mcp-config.ps1")

$resolvedConfigPath = Get-McpConfigPath -ConfigPath $ConfigPath

if (-not (Test-Path $resolvedConfigPath)) {
  throw "MCP config file not found: $resolvedConfigPath. Run .\setup-mcp.cmd first."
}

$entries = Get-McpServerEntries -ConfigPath $resolvedConfigPath

Write-Host "MCP config: $resolvedConfigPath"

if (-not $entries -or $entries.Count -eq 0) {
  Write-Host "No MCP servers are configured."
  exit 0
}

foreach ($entry in $entries) {
  $state = if ($entry.Enabled) { "enabled" } else { "disabled" }
  Write-Host ("- {0}: {1} | {2} | {3}" -f $entry.Name, $state, $entry.Transport, $entry.Target)
}
