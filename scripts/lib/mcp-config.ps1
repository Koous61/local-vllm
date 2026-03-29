Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "common.ps1")

function Get-McpConfigPath {
  param(
    [string]$ConfigPath = ""
  )

  if ($ConfigPath) {
    return $ConfigPath
  }

  return Join-Path (Get-ProjectRoot) "mcp-servers.json"
}

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

function Load-McpConfig {
  param(
    [string]$ConfigPath = ""
  )

  $path = Get-McpConfigPath -ConfigPath $ConfigPath
  $config = [ordered]@{
    mcpServers = [ordered]@{}
  }

  if (Test-Path $path) {
    $raw = Get-Content -Path $path -Raw
    if ($raw.Trim()) {
      $existing = ConvertFrom-Json -InputObject $raw
      $config = Convert-ToOrderedData -Value $existing
      if (-not $config.Contains("mcpServers")) {
        $config["mcpServers"] = [ordered]@{}
      }
    }
  }

  return $config
}

function Save-McpConfig {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,

    [Parameter(Mandatory = $true)]
    $Config
  )

  $json = $Config | ConvertTo-Json -Depth 12
  Set-Content -Path $ConfigPath -Value $json -Encoding ascii
}
