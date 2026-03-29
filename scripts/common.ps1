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
