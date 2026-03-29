param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string]$ModelId,

  [string]$ServedModelName,
  [string]$HFToken,
  [switch]$TrustRemoteCode,
  [switch]$NoRestart,
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lib\common.ps1")

$envFile = Ensure-EnvFile
$effectiveServedModelName = if ($ServedModelName) {
  $ServedModelName
} else {
  Convert-ToServedModelName -ModelId $ModelId
}

Set-DotEnvValue -Path $envFile -Key "MODEL_ID" -Value $ModelId
Set-DotEnvValue -Path $envFile -Key "SERVED_MODEL_NAME" -Value $effectiveServedModelName
Set-DotEnvValue -Path $envFile -Key "TRUST_REMOTE_CODE" -Value ($TrustRemoteCode.IsPresent.ToString().ToLowerInvariant())

if ($PSBoundParameters.ContainsKey("HFToken")) {
  Set-DotEnvValue -Path $envFile -Key "HF_TOKEN" -Value $HFToken
}

Write-Host "Updated model settings in $envFile"
Write-Host "  MODEL_ID=$ModelId"
Write-Host "  SERVED_MODEL_NAME=$effectiveServedModelName"
Write-Host "  TRUST_REMOTE_CODE=$($TrustRemoteCode.IsPresent.ToString().ToLowerInvariant())"

if ($PSBoundParameters.ContainsKey("HFToken")) {
  if ($HFToken) {
    Write-Host "  HF_TOKEN updated"
  } else {
    Write-Host "  HF_TOKEN cleared"
  }
}

if ($NoRestart) {
  Write-Host "Config updated. Restart skipped because -NoRestart was used."
  exit 0
}

Write-Host "Applying the model change to the running stack..."
& (Join-Path $PSScriptRoot "start.ps1") -TimeoutSeconds $TimeoutSeconds
exit $LASTEXITCODE
