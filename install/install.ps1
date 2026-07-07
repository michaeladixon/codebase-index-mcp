# install.ps1 -- installs the Claude Code token-optimization system (Windows).
# PowerShell 5.1 compatible. Idempotent: safe to re-run.
#
#   1. pip-installs the codebase-index-mcp package (upgrades if present)
#   2. copies the index_guard.py PreToolUse hook to %USERPROFILE%\.claude\hooks\
#   3. merges the hook block into %USERPROFILE%\.claude\settings.json (backs it up first)
#   4. registers the MCP server user-scope via `claude mcp add`
#   5. runs apply-global.py (SessionStart auto-index hook + user-level CLAUDE.md policy)
#
# Usage:  powershell -ExecutionPolicy Bypass -File install\install.ps1

$ErrorActionPreference = 'Stop'

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$PkgDir     = Join-Path $RepoRoot 'codebase-index-mcp'
$HookSrc    = Join-Path $RepoRoot 'enforcement\hooks\index_guard.py'
$ClaudeDir  = Join-Path $env:USERPROFILE '.claude'
$HooksDir   = Join-Path $ClaudeDir 'hooks'
$HookDst    = Join-Path $HooksDir 'index_guard.py'
$Settings   = Join-Path $ClaudeDir 'settings.json'
# IMPORTANT: expand to the literal absolute path. Claude Code does NOT expand
# %USERPROFILE% in hook commands; an unexpanded path makes python exit 2, and
# exit 2 on PreToolUse BLOCKS every Read/Grep/Glob call.
$HookCmd    = 'python "' + $env:USERPROFILE + '\.claude\hooks\index_guard.py"'

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# Run a native command with stderr silenced. Needed because under
# $ErrorActionPreference='Stop', PS 5.1 can turn redirected native stderr into a
# terminating error. Sets $LASTEXITCODE as usual.
function Invoke-Quiet([scriptblock]$Block) {
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block *> $null } finally { $ErrorActionPreference = $eap }
}

# --- 1. prerequisites -------------------------------------------------------
Write-Host '[1/5] Checking python + pip...'
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { Fail 'python not found on PATH. Install Python 3.10+ from python.org and re-run.' }
$pyver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
Write-Host "  python $pyver at $($python.Source)"
Invoke-Quiet { python -m pip --version }
if ($LASTEXITCODE -ne 0) { Fail 'pip not available (python -m pip failed). Install pip and re-run.' }

# --- 2. install the package -------------------------------------------------
Write-Host "[2/5] Installing codebase-index-mcp from $PkgDir ..."
if (-not (Test-Path (Join-Path $PkgDir 'pyproject.toml'))) { Fail "package not found at $PkgDir" }
& python -m pip install --upgrade "$PkgDir"
if ($LASTEXITCODE -ne 0) { Fail 'pip install failed (see output above).' }

# --- 3. hook file + settings.json merge -------------------------------------
Write-Host '[3/5] Installing PreToolUse hook...'
if (-not (Test-Path $HooksDir)) { New-Item -ItemType Directory -Path $HooksDir -Force | Out-Null }
Copy-Item $HookSrc $HookDst -Force
Write-Host "  copied index_guard.py -> $HookDst"

# Load existing settings (or start fresh), back up before touching.
$settingsObj = $null
if (Test-Path $Settings) {
    $raw = Get-Content $Settings -Raw
    if ($raw -and $raw.Trim()) {
        try { $settingsObj = $raw | ConvertFrom-Json }
        catch { Fail "$Settings is not valid JSON; fix or remove it, then re-run." }
    }
    $stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = "$Settings.bak-$stamp"
    Copy-Item $Settings $backup -Force
    Write-Host "  backed up settings.json -> $backup"
}
if (-not $settingsObj) { $settingsObj = New-Object PSObject }

if (-not ($settingsObj.PSObject.Properties.Name -contains 'hooks')) {
    $settingsObj | Add-Member -NotePropertyName hooks -NotePropertyValue (New-Object PSObject)
}
if (-not ($settingsObj.hooks.PSObject.Properties.Name -contains 'PreToolUse')) {
    $settingsObj.hooks | Add-Member -NotePropertyName PreToolUse -NotePropertyValue @()
}

# Skip if an index_guard entry is already wired in (idempotency).
$already = $false
foreach ($entry in @($settingsObj.hooks.PreToolUse)) {
    foreach ($h in @($entry.hooks)) {
        if ($h.command -and $h.command -match 'index_guard\.py') { $already = $true }
    }
}
if ($already) {
    Write-Host '  hook already present in settings.json -- skipping merge'
} else {
    $HookCmdJson = ($HookCmd -replace '\\', '\\') -replace '"', '\"'
    $entryJson = @"
{
  "matcher": "Read|Grep|Glob",
  "hooks": [
    { "type": "command", "command": "$HookCmdJson", "timeout": 10 }
  ]
}
"@
    $newEntry = $entryJson | ConvertFrom-Json
    $settingsObj.hooks.PreToolUse = @($settingsObj.hooks.PreToolUse) + $newEntry
    $json = $settingsObj | ConvertTo-Json -Depth 20
    # WriteAllText -> UTF-8 without BOM (PS 5.1's Set-Content -Encoding UTF8 adds a BOM, which breaks some JSON parsers)
    [System.IO.File]::WriteAllText($Settings, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "  merged PreToolUse hook into $Settings"
}

# --- 4. register the MCP server (user scope) --------------------------------
Write-Host '[4/5] Registering MCP server...'
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    Invoke-Quiet { claude mcp get codebase-index }
    if ($LASTEXITCODE -eq 0) {
        Write-Host '  codebase-index already registered -- skipping'
    } else {
        & claude mcp add codebase-index --scope user -- python -m codebase_index
        if ($LASTEXITCODE -ne 0) { Write-Host '  WARNING: claude mcp add failed; add manually (snippet below).' }
        else { Write-Host '  registered codebase-index (user scope)' }
    }
} else {
    Write-Host '  claude CLI not found on PATH. Add this to a .mcp.json in your project root'
    Write-Host '  (or to "mcpServers" in your Claude config) manually:'
    Write-Host @'
  {
    "mcpServers": {
      "codebase-index": {
        "command": "python",
        "args": ["-m", "codebase_index"],
        "env": { "PYTHONUNBUFFERED": "1" }
      }
    }
  }
'@
}

# --- 5. global auto-apply (SessionStart hook + user-level CLAUDE.md) ---------
Write-Host '[5/5] Applying global auto-index layer...'
& python (Join-Path $PSScriptRoot 'apply-global.py')
if ($LASTEXITCODE -ne 0) { Write-Host '  WARNING: apply-global.py failed; run manually: python install\apply-global.py' }

# --- done --------------------------------------------------------------------
Write-Host ''
Write-Host 'Install complete. Next steps:' -ForegroundColor Green
Write-Host '  1. Index your project:      powershell -File install\index-project.ps1 C:\path\to\your\project'
Write-Host '     (or: python -m codebase_index index C:\path\to\your\project)'
Write-Host "  2. (optional) Per-project policy append -- the global CLAUDE.md policy already covers all projects:"
Write-Host "     type enforcement\CLAUDE-policy.md >> C:\path\to\your\project\CLAUDE.md"
Write-Host '  3. Optional env levers:     see enforcement\env-recommendations.md'
Write-Host '     (MAX_MCP_OUTPUT_TOKENS, CLAUDE_CODE_SUBAGENT_MODEL, ...)'
Write-Host '  To disable the guard temporarily: set INDEX_GUARD_DISABLE=1'
