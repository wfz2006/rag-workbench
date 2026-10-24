param([int]$Port = 8505)
$ErrorActionPreference = 'Stop'
$settingsPath = Join-Path $PSScriptRoot 'knowledge_packs\hongloumeng\settings.json'
$frozen = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$keys = @('RAG_DATA_DIR') + @($frozen.PSObject.Properties | ForEach-Object { 'RAG_' + $_.Name.ToUpperInvariant() })
$old = @{}
foreach ($key in $keys) {
    $old[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
}
try {
    $env:RAG_DATA_DIR = Join-Path $PSScriptRoot 'data\hongloumeng'
    foreach ($property in $frozen.PSObject.Properties) {
        $value = [Convert]::ToString($property.Value, [Globalization.CultureInfo]::InvariantCulture)
        if ($property.Value -is [bool]) { $value = $value.ToLowerInvariant() }
        if ($property.Name -in @('domain_profile_path', 'retrieval_expansions_path')) { $value = Join-Path (Split-Path -Parent $settingsPath) $value }
        [Environment]::SetEnvironmentVariable(('RAG_' + $property.Name.ToUpperInvariant()), $value, 'Process')
    }
    & (Join-Path $PSScriptRoot 'start.ps1') -Port $Port
} finally {
    foreach ($key in $keys) {
        [Environment]::SetEnvironmentVariable($key, $old[$key], 'Process')
    }
}
