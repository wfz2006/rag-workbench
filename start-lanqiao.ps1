param([int]$Port = 8502)
$ErrorActionPreference = 'Stop'
$taskOldData = $env:RAG_DATA_DIR
$taskOldProfile = $env:RAG_TEACHING_PROFILE_PATH
$taskOldGeneral = $env:RAG_ALLOW_GENERAL
$taskOldContext = $env:RAG_RETRIEVAL_CONTEXT_WINDOW
try {
    $env:RAG_DATA_DIR = Join-Path $PSScriptRoot 'data\lanqiao_ds'
    $env:RAG_TEACHING_PROFILE_PATH = Join-Path $PSScriptRoot 'knowledge_packs\lanqiao_ds\profile\teaching.md'
    $env:RAG_ALLOW_GENERAL = 'false'
    $env:RAG_RETRIEVAL_CONTEXT_WINDOW = '2'
    & (Join-Path $PSScriptRoot 'start.ps1') -Port $Port
} finally {
    $env:RAG_DATA_DIR = $taskOldData
    $env:RAG_TEACHING_PROFILE_PATH = $taskOldProfile
    $env:RAG_ALLOW_GENERAL = $taskOldGeneral
    $env:RAG_RETRIEVAL_CONTEXT_WINDOW = $taskOldContext
}
