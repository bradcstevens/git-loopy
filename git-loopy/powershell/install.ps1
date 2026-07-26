#!/usr/bin/env pwsh

<#
.SYNOPSIS
    Installer for git-loopy's PowerShell distribution (ADR-0013, PRD #173).

.DESCRIPTION
    One command installs the two halves of this clone's distribution: a
    `git-loopy` launcher on your PATH, and the `git-loopy-tui` helper this
    clone's Release pins, staged into `.git-loopy/bin/` where the Orchestrator
    looks for it first.

    The launcher is a small shim that invokes this clone's git-loopy.ps1 by
    absolute path. On Windows the shim is a `git-loopy.cmd` batch file; on Linux
    and macOS it is a `git-loopy` script with a `pwsh` shebang. Either way the
    shim points back into this clone so the shared git-loopy/PROMPT.md keeps
    resolving one directory above the launcher — the installer never copies the
    Orchestrator out of the tree.

    The helper is the only thing this script downloads, it is downloaded exactly
    once here, and a Run never downloads anything at all. `-NoTui` skips it;
    `-TuiArchive`/`-TuiChecksum` install it from files an air-gapped host already
    has. Either way the artifact has to prove its published checksum, this
    clone's exact Release version, and Event-schema compatibility before it
    replaces anything — and a failure at any of those steps leaves a previously
    installed helper exactly as it was, with the Orchestrator still runnable in
    plain mode.

.PARAMETER BinDir
    Directory to install the launcher into. Defaults to $HOME\bin on Windows and
    $XDG_BIN_HOME (else ~/.local/bin) on Linux and macOS.

.PARAMETER NoTui
    Install only the launcher. Runs stay in plain mode.

.PARAMETER TuiArchive
    Install the helper from a local release archive instead of downloading it.
    Requires -TuiChecksum.

.PARAMETER TuiChecksum
    The archive's published `.sha256` manifest.

.PARAMETER TuiBaseUrl
    Fetch the helper from somewhere other than this Release's published download
    location.

.EXAMPLE
    pwsh -NoLogo -NoProfile -File ./install.ps1

.EXAMPLE
    pwsh -NoLogo -NoProfile -File ./install.ps1 -BinDir ~/.local/bin -NoTui
#>

[CmdletBinding()]
param(
    [string]$BinDir,
    [switch]$NoTui,
    [string]$TuiArchive,
    [string]$TuiChecksum,
    [string]$TuiBaseUrl
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
$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
$ArtifactMetadata = Join-Path $RepositoryRoot "git-loopy/conformance/tui-artifacts.json"

Import-Module (Join-Path $PSScriptRoot "GitLoopy.Release.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "GitLoopy.Events.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "GitLoopy.TuiInstall.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "GitLoopy.Continuation.psm1") -Force

# Setup verifies the one native distribution it is installing (#257). The
# distribution being verified is this clone — nothing resolves an entrypoint and
# nothing names a family member, so the selection is expressed by which installer
# the operator ran and is never written down. It runs before anything is created
# so a distribution that cannot do the foundation work installs nothing at all.
if (-not (Test-GitLoopyDistributionCapabilities -Name foundation)) {
    exit 1
}

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

# The helper is installed after the launcher because the launcher is the part
# that has to work: a Run without a helper is a Run in plain mode, while a helper
# without a launcher is nothing at all.
if ($NoTui) {
    [Console]::Out.WriteLine(
        "Skipped git-loopy-tui (-NoTui). Runs stay in plain mode.")
}
else {
    $ReleaseVersion = try {
        Get-GitLoopyReleaseVersion
    }
    catch {
        [Console]::Error.WriteLine(
            "install.ps1: cannot determine which Release this clone pins")
        exit 1
    }

    try {
        $HelperPath = Install-GitLoopyTuiHelper `
            -Metadata $ArtifactMetadata `
            -RepositoryRoot $RepositoryRoot `
            -ReleaseVersion $ReleaseVersion `
            -SchemaVersion (Get-GitLoopyEventSchemaVersion) `
            -BaseUrl $TuiBaseUrl `
            -Archive $TuiArchive `
            -Checksum $TuiChecksum
        [Console]::Out.WriteLine(
            "Installed git-loopy-tui ${ReleaseVersion}: $HelperPath")
    }
    catch {
        [Console]::Error.WriteLine("install.ps1: $($_.Exception.Message)")
        [Console]::Error.WriteLine(
            "install.ps1: could not install the git-loopy-tui $ReleaseVersion helper.")
        [Console]::Error.WriteLine(
            "  Nothing was replaced; git-loopy still runs, in plain mode, without it.")
        [Console]::Error.WriteLine(
            "  Re-run with -NoTui to install the launcher alone.")
        exit 1
    }
}

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
