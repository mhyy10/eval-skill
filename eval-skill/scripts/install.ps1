<#
.SYNOPSIS
  Install eval-skill into an agent's skills directory.

.EXAMPLE
  .\scripts\install.ps1
  .\scripts\install.ps1 -Target codex
  .\scripts\install.ps1 -Dest D:\my-skills\eval-skill
#>
param(
  [ValidateSet("claude", "codex")]
  [string]$Target = "claude",
  [string]$Dest = ""
)

$ErrorActionPreference = "Stop"

if (-not $Dest) {
  switch ($Target) {
    "claude" { $Dest = Join-Path $env:USERPROFILE ".claude\skills\eval-skill" }
    "codex"  { $Dest = Join-Path $env:USERPROFILE ".codex\skills\eval-skill" }
  }
}

$Src = Resolve-Path (Join-Path $PSScriptRoot "..")

New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null

if (Test-Path $Dest) {
  Write-Error "Destination already exists: $Dest`nRemove it first or pass -Dest to install elsewhere."
  exit 1
}

# Try a symlink first so repo updates propagate; fall back to copy.
try {
  New-Item -ItemType SymbolicLink -Path $Dest -Target $Src -ErrorAction Stop | Out-Null
  Write-Host "Linked $Dest -> $Src"
} catch {
  Copy-Item -Recurse $Src $Dest
  Write-Host "Copied $Src -> $Dest (symlink unavailable: $($_.Exception.Message))"
}

Write-Host ""
Write-Host "Installed eval-skill. Try it:"
Write-Host "  python $Dest\scripts\eval.py run --skill <skill-dir> --fixture $Dest\fixtures\edit-article-clarity --cli mock"
