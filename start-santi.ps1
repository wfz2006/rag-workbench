param([int]$Port = 8503, [int]$SpoilerPage = 0)
$ErrorActionPreference = 'Stop'
$settingsPath = Join-Path $PSScriptRoot 'knowledge_packs\santi\settings.json'
$frozen = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$keys = @('RAG_DATA_DIR', 'RAG_SPOILER_MAX_PAGE') + @($frozen.PSObject.Properties | ForEach-Object { 'RAG_' + $_.Name.ToUpperInvariant() })
$old = @{}
foreach ($key in $keys) {
    $old[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
}
try {
    $env:RAG_DATA_DIR = Join-Path $PSScriptRoot 'data\santi'
    $env:RAG_SPOILER_MAX_PAGE = if ($SpoilerPage -gt 0) { "$SpoilerPage" } else { $null }
    foreach ($property in $frozen.PSObject.Properties) {
        $value = [Convert]::ToString($property.Value, [Globalization.CultureInfo]::InvariantCulture)
        if ($property.Value -is [bool]) { $value = $value.ToLowerInvariant() }
        if ($property.Name -eq 'domain_profile_path') { $value = Join-Path (Split-Path -Parent $settingsPath) $value }
        [Environment]::SetEnvironmentVariable(('RAG_' + $property.Name.ToUpperInvariant()), $value, 'Process')
    }
    & (Join-Path $PSScriptRoot 'start.ps1') -Port $Port
} finally {
    foreach ($key in $keys) {
        [Environment]::SetEnvironmentVariable($key, $old[$key], 'Process')
    }
}
