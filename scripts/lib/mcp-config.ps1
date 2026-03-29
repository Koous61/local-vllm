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

function Get-McpServerEntries {
  param(
    [string]$ConfigPath = ""
  )

  $config = Load-McpConfig -ConfigPath $ConfigPath
  $entries = @()

  foreach ($property in $config["mcpServers"].GetEnumerator()) {
    $serverConfig = $property.Value
    $transport = if ($serverConfig.Contains("transport") -and $serverConfig["transport"]) {
      [string]$serverConfig["transport"]
    } elseif ($serverConfig.Contains("url") -and $serverConfig["url"]) {
      "streamable-http"
    } else {
      "stdio"
    }

    $target = if ($transport -in @("streamable-http", "streamable_http", "http")) {
      [string]$serverConfig["url"]
    } else {
      [string]$serverConfig["command"]
    }

    $entries += [pscustomobject]@{
      Name = [string]$property.Key
      Enabled = [bool](
        -not $serverConfig.Contains("enabled") -or
        $serverConfig["enabled"] -eq $true -or
        (
          $serverConfig["enabled"] -is [string] -and
          $serverConfig["enabled"].Trim().ToLowerInvariant() -notin @("false", "0", "no", "off")
        )
      )
      Transport = $transport
      Target = $target
    }
  }

  return $entries | Sort-Object Name
}

function Set-McpServerEnabledState {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$Name,

    [Parameter(Mandatory = $true)]
    [bool]$Enabled,

    [string]$ConfigPath = ""
  )

  $path = Get-McpConfigPath -ConfigPath $ConfigPath
  $config = Load-McpConfig -ConfigPath $path
  $missing = @()

  foreach ($item in $Name) {
    $trimmed = $item.Trim()
    if (-not $trimmed) {
      continue
    }

    if (-not $config["mcpServers"].Contains($trimmed)) {
      $missing += $trimmed
      continue
    }

    $config["mcpServers"][$trimmed]["enabled"] = $Enabled
  }

  if ($missing.Count -gt 0) {
    $available = @($config["mcpServers"].Keys) | Sort-Object
    $availableText = if ($available.Count -gt 0) { $available -join ", " } else { "none" }
    throw "Unknown MCP server(s): $($missing -join ', '). Available servers: $availableText"
  }

  Save-McpConfig -ConfigPath $path -Config $config
  return Get-McpServerEntries -ConfigPath $path | Where-Object { $_.Name -in $Name }
}
