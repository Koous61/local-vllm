param(
  [string[]]$AllowedPath = @(),
  [string]$Name = "filesystem",
  [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Convert-ToOrderedData {
  param(
    [Parameter(Mandatory = $true)]
    $Value
  )

  if ($null -eq $Value) {
    return $null
  }

  if ($Value -is [string] -or $Value -is [ValueType]) {
    return $Value
  }

  if ($Value -is [pscustomobject] -or $Value -is [System.Collections.IDictionary]) {
    $map = [ordered]@{}
    foreach ($property in $Value.PSObject.Properties) {
      $map[$property.Name] = Convert-ToOrderedData -Value $property.Value
    }
    return $map
  }

  if ($Value -is [System.Collections.IEnumerable]) {
    $items = @()
    foreach ($item in $Value) {
      $items += ,(Convert-ToOrderedData -Value $item)
    }
    return $items
  }

  return $Value
}

$root = Get-ProjectRoot
if (-not $ConfigPath) {
  $ConfigPath = Join-Path $root "mcp-servers.json"
}

if (-not $AllowedPath -or $AllowedPath.Count -eq 0) {
  $AllowedPath = @($root)
}

$resolvedPaths = @()
foreach ($path in $AllowedPath) {
  $resolvedPaths += (Resolve-Path $path).Path
}

$config = [ordered]@{
  mcpServers = [ordered]@{}
}

if (Test-Path $ConfigPath) {
  $raw = Get-Content -Path $ConfigPath -Raw
  if ($raw.Trim()) {
    $existing = ConvertFrom-Json -InputObject $raw
    $config = Convert-ToOrderedData -Value $existing
    if (-not $config.Contains("mcpServers")) {
      $config["mcpServers"] = [ordered]@{}
    }
  }
}

$args = @("-y", "@modelcontextprotocol/server-filesystem") + $resolvedPaths
$config["mcpServers"][$Name] = [ordered]@{
  transport = "stdio"
  command = "npx"
  args = $args
}

$json = $config | ConvertTo-Json -Depth 12
Set-Content -Path $ConfigPath -Value $json -Encoding ascii

Write-Host "Updated $ConfigPath"
Write-Host "Filesystem MCP allowed paths:"
foreach ($path in $resolvedPaths) {
  Write-Host "  - $path"
}
