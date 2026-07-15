# Deploy Heating Assistant custom component to a local Home Assistant config.
# Usage:
#   .\scripts\upd.ps1
#   .\scripts\upd.ps1 -ConfigPath C:\path\to\ha\config
#   $env:HA_CONFIG = 'C:\path\to\ha\config'; .\scripts\upd.ps1

param(
    [string]$ConfigPath = $env:HA_CONFIG
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $RepoRoot 'custom_components\heating_assistant'

if (-not $ConfigPath) {
    $candidates = @(
        "$env:USERPROFILE\hass_config",
        "$env:USERPROFILE\.homeassistant",
        'C:\config',
        '/config'
    )
    foreach ($candidate in $candidates) {
        if (Test-Path (Join-Path $candidate 'configuration.yaml')) {
            $ConfigPath = $candidate
            break
        }
    }
}

if (-not $ConfigPath -or -not (Test-Path $ConfigPath)) {
    Write-Error "Home Assistant config path not found. Pass -ConfigPath or set HA_CONFIG."
}

$TargetRoot = Join-Path $ConfigPath 'custom_components'
$Target = Join-Path $TargetRoot 'heating_assistant'
New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null

Write-Host "Deploying $Source -> $Target"
if (Test-Path $Target) {
    Remove-Item -Recurse -Force $Target
}
Copy-Item -Recurse -Force $Source $Target
Write-Host "Done. Reload Heating Assistant in HA: Developer Tools -> YAML -> Heating Assistant: Reload"
Write-Host "Or restart Home Assistant to pick up backend persistence changes."
