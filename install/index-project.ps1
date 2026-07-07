# index-project.ps1 -- build/refresh the codebase index for a project and print stats.
# Usage:  powershell -File install\index-project.ps1 [C:\path\to\project] [-Full]
param(
    [string]$ProjectPath = '.',
    [switch]$Full
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path $ProjectPath -PathType Container)) {
    Write-Host "ERROR: not a directory: $ProjectPath" -ForegroundColor Red; exit 1
}
$ProjectPath = (Resolve-Path $ProjectPath).Path

Write-Host "Indexing $ProjectPath ..."
$idxArgs = @('-m', 'codebase_index', 'index', $ProjectPath)
if ($Full) { $idxArgs += '--full' }
& python @idxArgs
if ($LASTEXITCODE -ne 0) { Write-Host 'ERROR: indexing failed.' -ForegroundColor Red; exit 1 }

Write-Host ''
Write-Host 'Index status:'
& python -m codebase_index status $ProjectPath
