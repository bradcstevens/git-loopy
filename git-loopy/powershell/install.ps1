#!/usr/bin/env pwsh

<#
.SYNOPSIS
    Optional installer for git-loopy's PowerShell Orchestrator (ADR-0013).

.DESCRIPTION
    Puts a single `git-loopy` command on your PATH by writing a small launcher
    shim that invokes this clone's git-loopy.ps1 by absolute path. It installs
    NOTHING else — no Python, no TUI helper (git-loopy-tui arrives in phase 2),
    and no package-manager distribution. Run-in-place from the clone stays the
    baseline; this is a convenience so you can type `git-loopy` from any repo.

    On Windows the shim is a `git-loopy.cmd` batch file; on Linux and macOS it is
    a `git-loopy` script with a `pwsh` shebang. Either way the shim points back
    into this clone so the shared git-loopy/PROMPT.md keeps resolving one
    directory above the launcher — the installer never copies the Orchestrator
    out of the tree.

.PARAMETER BinDir
    Directory to install the launcher into. Defaults to $HOME\bin on Windows and
    $XDG_BIN_HOME (else ~/.local/bin) on Linux and macOS.

.EXAMPLE
    pwsh -NoLogo -NoProfile -File ./install.ps1

.EXAMPLE
    pwsh -NoLogo -NoProfile -File ./install.ps1 -BinDir ~/.local/bin
#>

[CmdletBinding()]
param(
    [string]$BinDir
)

if ($PSVersionTable.PSVersion.Major -lt 7) {
    [Console]::Error.WriteLine(
        "git-loopy's installer requires PowerShell 7+ " +
        "(found $($PSVersionTable.PSVersion)). Install PowerShell 7 and " +
        "rerun this script with pwsh."
    )
    exit 1
}

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Launcher = Join-Path $PSScriptRoot "git-loopy.ps1"
if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    [Console]::Error.WriteLine("install.ps1: launcher not found at $Launcher")
    exit 1
}
$Launcher = (Resolve-Path -LiteralPath $Launcher).Path

if ([string]::IsNullOrWhiteSpace($BinDir)) {
    if ($IsWindows) {
        $BinDir = Join-Path $HOME "bin"
    }
    elseif (-not [string]::IsNullOrWhiteSpace($env:XDG_BIN_HOME)) {
        $BinDir = $env:XDG_BIN_HOME
    }
    else {
        $BinDir = Join-Path $HOME ".local/bin"
    }
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$BinDir = (Resolve-Path -LiteralPath $BinDir).Path

# Write the shim without a BOM: a BOM before a `#!` line breaks the shebang, and
# batch files expect plain ASCII/UTF-8.
$NoBom = [System.Text.UTF8Encoding]::new($false)

if ($IsWindows) {
    $Shim = Join-Path $BinDir "git-loopy.cmd"
    $Content = "@echo off`r`n" +
        "pwsh -NoLogo -NoProfile -File `"$Launcher`" %*`r`n"
    [IO.File]::WriteAllText($Shim, $Content, $NoBom)
}
else {
    $Shim = Join-Path $BinDir "git-loopy"
    $Content = "#!/usr/bin/env pwsh`n" +
        "& `"$Launcher`" @args`n" +
        "exit `$LASTEXITCODE`n"
    [IO.File]::WriteAllText($Shim, $Content, $NoBom)
    & chmod +x $Shim
    if ($LASTEXITCODE -ne 0) {
        throw "install.ps1: failed to mark $Shim executable (chmod exit $LASTEXITCODE)"
    }
}

[Console]::Out.WriteLine("Installed git-loopy launcher: $Shim")
[Console]::Out.WriteLine("  -> $Launcher")

$Separator = [IO.Path]::PathSeparator
$Comparison = if ($IsWindows) {
    [StringComparison]::OrdinalIgnoreCase
}
else {
    [StringComparison]::Ordinal
}
$OnPath = $false
foreach ($Entry in ($env:PATH -split [regex]::Escape($Separator))) {
    if ([string]::IsNullOrEmpty($Entry)) { continue }
    try { $Resolved = [IO.Path]::GetFullPath($Entry) } catch { $Resolved = $Entry }
    if ([string]::Equals(
            $Resolved.TrimEnd([IO.Path]::DirectorySeparatorChar),
            $BinDir.TrimEnd([IO.Path]::DirectorySeparatorChar),
            $Comparison)) {
        $OnPath = $true
        break
    }
}

if ($OnPath) {
    [Console]::Out.WriteLine(
        "Run it from inside any git repository: git-loopy")
}
else {
    [Console]::Out.WriteLine("")
    [Console]::Out.WriteLine(
        "$BinDir is not on your PATH. Add it, then reopen your shell:")
    if ($IsWindows) {
        [Console]::Out.WriteLine(
            "  `$env:PATH = `"$BinDir;`$env:PATH`"   # current session")
        [Console]::Out.WriteLine(
            "  setx PATH `"$BinDir;`$env:PATH`"      # persist for new sessions")
    }
    else {
        [Console]::Out.WriteLine("  export PATH=`"$BinDir`:`$PATH`"")
    }
    [Console]::Out.WriteLine("Until then, run the launcher directly: $Shim")
}
