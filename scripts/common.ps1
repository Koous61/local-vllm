Set-StrictMode -Version Latest

function Get-ProjectRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-EnvFile {
  return Join-Path (Get-ProjectRoot) ".env"
}

function Ensure-EnvFile {
  $envFile = Get-EnvFile
  if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path (Get-ProjectRoot) ".env.example") $envFile
    Write-Host "Created $envFile from .env.example. Edit it if you want another model or token."
  }

  return $envFile
}

function Read-DotEnv {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  $values = @{}
  foreach ($rawLine in Get-Content -Path $Path) {
    $line = $rawLine.Trim()
    if (-not $line -or $line.StartsWith("#")) {
      continue
    }

    $separator = $line.IndexOf("=")
    if ($separator -lt 1) {
      continue
    }

    $key = $line.Substring(0, $separator).Trim()
    $value = $line.Substring($separator + 1).Trim()
    $values[$key] = $value
  }

  return $values
}

function Get-ComposeArgs {
  $root = Get-ProjectRoot
  $envFile = Ensure-EnvFile
  return @("--project-directory", $root, "--env-file", $envFile)
}

function Set-DotEnvValue {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,

    [Parameter(Mandatory = $true)]
    [string]$Key,

    [Parameter(Mandatory = $true)]
    [string]$Value
  )

  $updated = $false
  $lines = New-Object System.Collections.Generic.List[string]
  $escapedKey = [regex]::Escape($Key)

  foreach ($line in Get-Content -Path $Path) {
    if ($line -match "^${escapedKey}=") {
      $lines.Add("$Key=$Value")
      $updated = $true
    } else {
      $lines.Add($line)
    }
  }

  if (-not $updated) {
    $lines.Add("$Key=$Value")
  }

  Set-Content -Path $Path -Value $lines -Encoding ascii
}

function Convert-ToServedModelName {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ModelId
  )

  $trimmed = $ModelId.Trim()
  $leaf = Split-Path -Path ($trimmed -replace "\\", "/") -Leaf
  if (-not $leaf) {
    $leaf = $trimmed
  }

  $servedName = $leaf.ToLowerInvariant()
  $servedName = $servedName -replace "[^a-z0-9._-]+", "-"
  $servedName = $servedName -replace "-{2,}", "-"
  $servedName = $servedName.Trim("-")

  if (-not $servedName) {
    throw "Could not derive a served model name from '$ModelId'."
  }

  return $servedName
}
