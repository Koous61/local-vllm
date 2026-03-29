param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string[]]$Name,

  [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\mcp-config.ps1")

$resolvedConfigPath = Get-McpConfigPath -ConfigPath $ConfigPath
$updated = Set-McpServerEnabledState -Name $Name -Enabled $false -ConfigPath $resolvedConfigPath

Write-Host "Updated $resolvedConfigPath"
foreach ($entry in $updated) {
  Write-Host ("- {0}: disabled" -f $entry.Name)
}
