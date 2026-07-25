Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot "GitLoopy.Events.psm1") -Force

# The shared TUI helper's supervision seam (PRD #173, ADR-0013).
#
# The PowerShell Orchestrator does not draw anything. It decides whether this Run
# wants a live interface, finds a `git-loopy-tui` it is allowed to trust, starts
# it as a child, and routes the already-serialized Event stream into that child's
# stdin instead of stdout. Everything the child does with those bytes — raw mode,
# the alternate screen, key input, restoration — belongs to the child.
#
# The whole module exists to keep one promise: a live interface is *presentation*
# and can never make a Run fail. Every failure path here ends in raw JSONL on
# stdout with the replay log untouched, and never in a non-zero Run.

# The clone-local helper the repository pins, relative to the repository root.
$Script:TuiCloneRelativeDirectory = ".git-loopy/bin"
$Script:TuiCommandName = "git-loopy-tui"
# Windows names an executable by extension, so the pinned artefact is one of
# these rather than the bare command name the POSIX ports install. The order is
# preference order: a native binary outranks a shim that would have to launch
# one.
$Script:TuiWindowsExtensions = @(".exe", ".com", ".cmd", ".bat")

# A file the operating system would actually run. On Windows the extension is
# the whole answer; elsewhere the execute bit is, and a helper that exists but
# cannot be executed must not shadow a working one further down the search
# order. `GetUnixFileMode` arrived in .NET 7, so a PowerShell 7.0-7.2 host
# degrades to existence — which can only ever *widen* discovery, and the
# `--schema-version` probe is still the gate that has to be passed.
function Test-GitLoopyTuiExecutable {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    if (-not [IO.File]::Exists($Path)) {
        return $false
    }
    if ($IsWindows) {
        return $true
    }
    try {
        $Mode = [IO.File]::GetUnixFileMode($Path)
        $Executable = (
            [IO.UnixFileMode]::UserExecute -bor
            [IO.UnixFileMode]::GroupExecute -bor
            [IO.UnixFileMode]::OtherExecute
        )
        return ($Mode -band $Executable) -ne [IO.UnixFileMode]::None
    }
    catch {
        return $true
    }
}

function Find-GitLoopyTuiInDirectory {
    param(
        [Parameter(Mandatory)]
        [string]$Directory
    )

    $Names = if ($IsWindows) {
        @($Script:TuiWindowsExtensions | ForEach-Object {
            $Script:TuiCommandName + $_
        })
    }
    else {
        @($Script:TuiCommandName)
    }
    foreach ($Name in $Names) {
        $Candidate = Join-Path $Directory $Name
        if (Test-GitLoopyTuiExecutable -Path $Candidate) {
            return $Candidate
        }
    }
    return $null
}

# Mirrors `git_loopy.interactive.detect.resolve_interactive` and the shell port's# `git_loopy_tui_resolve_intent`: the explicit flag wins, then a non-blank
# `GIT_LOOPY_INTERACTIVE`, then whether stdout is a terminal. The `Source` field
# is what decides how loudly an unfulfillable request is reported, so it has to
# survive the resolution rather than be re-derived later.
function Resolve-GitLoopyTuiIntent {
    [CmdletBinding()]
    param(
        [AllowNull()]
        [AllowEmptyString()]
        [string]$Flag,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$EnvironmentValue,
        [bool]$IsTty
    )

    if (-not [string]::IsNullOrEmpty($Flag)) {
        return [pscustomobject]@{
            PSTypeName = "GitLoopy.TuiIntent"
            Intent = $Flag
            Source = "explicit"
        }
    }

    $Trimmed = if ($null -eq $EnvironmentValue) { "" } else {
        $EnvironmentValue.Trim()
    }
    if ($Trimmed.Length -gt 0) {
        $Intent = if (
            $Trimmed.ToLowerInvariant() -cin @("1", "true", "yes", "on")
        ) {
            "on"
        }
        else {
            "off"
        }
        return [pscustomobject]@{
            PSTypeName = "GitLoopy.TuiIntent"
            Intent = $Intent
            Source = "explicit"
        }
    }

    return [pscustomobject]@{
        PSTypeName = "GitLoopy.TuiIntent"
        Intent = if ($IsTty) { "on" } else { "off" }
        Source = "auto"
    }
}

# Clone-local first, then PATH. The clone-local helper is version-pinned by the
# repository, so it wins over whatever a package manager happens to have
# installed globally; both still have to pass the probe below.
#
# `SearchPath` is a parameter rather than a read of `$env:PATH` so a test can
# describe the machine it wants without mutating the process it runs in.
function Find-GitLoopyTuiHelper {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$SearchPath = $env:PATH
    )

    $Pinned = Join-Path $RepoRoot $Script:TuiCloneRelativeDirectory
    $Candidate = Find-GitLoopyTuiInDirectory -Directory $Pinned
    if ($null -ne $Candidate) {
        return [pscustomobject]@{
            PSTypeName = "GitLoopy.TuiHelper"
            Source = "clone-local"
            Path = $Candidate
        }
    }

    if ([string]::IsNullOrEmpty($SearchPath)) {
        return $null
    }
    foreach ($Directory in $SearchPath.Split([IO.Path]::PathSeparator)) {
        if ([string]::IsNullOrWhiteSpace($Directory)) {
            continue
        }
        $Candidate = Find-GitLoopyTuiInDirectory -Directory $Directory
        if ($null -ne $Candidate) {
            return [pscustomobject]@{
                PSTypeName = "GitLoopy.TuiHelper"
                Source = "path"
                Path = $Candidate
            }
        }
    }
    return $null
}

# The pre-fullscreen gate. `--schema-version` is the only helper invocation that
# is safe before a decision: it reads no stdin and touches no terminal, so a
# helper that turns out to be incompatible never gets to blank the screen first.
#
# The answer is a *range* because a later helper may decode more than one
# Event-schema version at once, so the test is containment, not equality.
# Returns the Release version the helper reports — the empty string when it
# reports none — or `$null` when the helper cannot be trusted with the stream.
function Test-GitLoopyTuiSchemaSupport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Helper
    )

    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Helper
    $StartInfo.ArgumentList.Add("--schema-version")
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true

    $Probe = $null
    try {
        $Process = [Diagnostics.Process]::new()
        try {
            $Process.StartInfo = $StartInfo
            if (-not $Process.Start()) {
                return $null
            }
            $Probe = $Process.StandardOutput.ReadToEnd()
            [void]$Process.StandardError.ReadToEnd()
            $Process.WaitForExit()
            if ($Process.ExitCode -ne 0) {
                return $null
            }
        }
        finally {
            $Process.Dispose()
        }
    }
    catch {
        return $null
    }

    try {
        $Parsed = $Probe | ConvertFrom-Json -AsHashtable
    }
    catch {
        return $null
    }
    if ($Parsed -isnot [Collections.IDictionary]) {
        return $null
    }
    foreach ($Key in @(
        "min_event_schema_version", "max_event_schema_version"
    )) {
        if (-not $Parsed.Contains($Key)) {
            return $null
        }
        # A JSON string bound is not a bound: comparing it would make PowerShell
        # coerce the *number* to a string and order it lexicographically, so
        # `"10"` would sort below `2`.
        if ($Parsed[$Key] -isnot [int] -and
            $Parsed[$Key] -isnot [long] -and
            $Parsed[$Key] -isnot [double] -and
            $Parsed[$Key] -isnot [decimal]) {
            return $null
        }
    }

    $Wanted = Get-GitLoopyEventSchemaVersion
    if ($Parsed["min_event_schema_version"] -gt $Wanted) {
        return $null
    }
    if ($Parsed["max_event_schema_version"] -lt $Wanted) {
        return $null
    }

    if ($Parsed.Contains("version") -and $Parsed["version"] -is [string]) {
        return [string]$Parsed["version"]
    }
    return ""
}

# Contract §16: Release equality is product identity, never a compatibility
# authority. A helper staged as part of *this* distribution must match exactly
# and fails closed on drift; an externally discovered one may still run on the
# strength of the schema probe, but the operator is told the Releases differ.
#
# The diagnostic is *returned* rather than written, so the decision and the
# sentence that explains it stay together while the one place that owns this
# module's stderr stays the one place that writes to it.
function Test-GitLoopyTuiReleaseIdentity {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Source,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$HelperVersion,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$ReleaseVersion
    )

    $Drifted = (
        -not [string]::IsNullOrEmpty($HelperVersion) -and
        -not [string]::IsNullOrEmpty($ReleaseVersion) -and
        $HelperVersion -cne $ReleaseVersion
    )
    if (-not $Drifted) {
        return [pscustomobject]@{
            PSTypeName = "GitLoopy.TuiReleaseIdentity"
            Trusted = $true
            Warning = $null
        }
    }
    if ($Source -ceq "clone-local") {
        return [pscustomobject]@{
            PSTypeName = "GitLoopy.TuiReleaseIdentity"
            Trusted = $false
            Warning = $null
        }
    }
    return [pscustomobject]@{
        PSTypeName = "GitLoopy.TuiReleaseIdentity"
        Trusted = $true
        Warning = (
            "the $Script:TuiCommandName helper on PATH reports Release " +
            "version $HelperVersion, not $ReleaseVersion; its Event-schema " +
            "support is compatible, so the live interface continues"
        )
    }
}

Export-ModuleMember -Function @(
    "Resolve-GitLoopyTuiIntent",
    "Find-GitLoopyTuiHelper",
    "Test-GitLoopyTuiSchemaSupport",
    "Test-GitLoopyTuiReleaseIdentity"
)