Set-StrictMode -Version Latest

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
#
# This module deliberately imports nothing. Its only inputs are its parameters,
# so the Event-schema version it negotiates against is passed in rather than read
# from `GitLoopy.Events.psm1`: two modules that each `Import-Module` the same
# third one can end up holding *different instances* of it, and module-scoped
# state — which is exactly what the live sink is — would then be set on one
# instance and read from the other.

# The clone-local helper the repository pins, relative to the repository root.
$Script:TuiCloneRelativeDirectory = ".git-loopy/bin"
$Script:TuiCommandName = "git-loopy-tui"
# Windows names an executable by extension, so the pinned artefact is one of
# these rather than the bare command name the POSIX ports install. The order is
# preference order: a native binary outranks a shim that would have to launch
# one.
$Script:TuiWindowsExtensions = @(".exe", ".com", ".cmd", ".bat")

$Script:TuiProcess = $null
$Script:TuiActive = $false
# Once a Run has fallen back it stays fallen back: a helper that died once is not
# a helper that will survive being started again, and a respawn mid-Run would
# split the live stream across two children.
$Script:TuiRetired = $false

function Write-GitLoopyTuiWarning {
    param(
        [Parameter(Mandatory)]
        [string]$Message
    )

    [Console]::Error.WriteLine("git-loopy: $Message")
}

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
        [string]$Helper,
        [Parameter(Mandatory)]
        [int]$SchemaVersion
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

    $Wanted = $SchemaVersion
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

# Only stdin is redirected: the child owns the terminal, so it must inherit the
# real stdout and stderr to draw at all. `StandardInputEncoding` is pinned rather
# than inherited from the console, because the Event stream is UTF-8 by contract
# and a Windows console code page would otherwise mangle it.
function Start-GitLoopyTuiProcess {
    param(
        [Parameter(Mandatory)]
        [string]$Helper
    )

    if ($Script:TuiRetired) {
        return $false
    }

    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Helper
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.StandardInputEncoding = [Text.UTF8Encoding]::new($false)

    try {
        $Process = [Diagnostics.Process]::new()
        $Process.StartInfo = $StartInfo
        if (-not $Process.Start()) {
            $Process.Dispose()
            return $false
        }
    }
    catch {
        return $false
    }

    $Script:TuiProcess = $Process
    $Script:TuiActive = $true
    return $true
}

# One diagnostic, one direction. The stream goes back to stdout permanently: a
# Run that fell back once never respawns the helper, because a second child
# would split the live stream in two. The installed sink is left in place and
# becomes a pass-through, so the caller has nothing to unwind.
function Invoke-GitLoopyTuiFallback {
    param(
        [Parameter(Mandatory)]
        [string]$Reason
    )

    if (-not $Script:TuiActive) {
        return
    }
    $Script:TuiActive = $false
    $Script:TuiRetired = $true
    Close-GitLoopyTuiInput
    Write-GitLoopyTuiWarning (
        "$Reason; continuing with raw JSONL output on stdout"
    )
}

function Close-GitLoopyTuiInput {
    if ($null -eq $Script:TuiProcess) {
        return
    }
    try {
        $Script:TuiProcess.StandardInput.Close()
    }
    catch {
        # A child that already died closed this end for us.
    }
}

# The live sink itself. The line that could not be delivered is not dropped: it
# goes to stdout with everything after it, so the raw stream a consumer sees is
# still complete from the operator's point of view.
function Write-GitLoopyTuiLine {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string]$Line
    )

    if (-not $Script:TuiActive) {
        [Console]::Out.Write($Line)
        return
    }

    try {
        $Script:TuiProcess.StandardInput.Write($Line)
        $Script:TuiProcess.StandardInput.Flush()
    }
    catch {
        Invoke-GitLoopyTuiFallback -Reason (
            "the $Script:TuiCommandName helper stopped reading the Event stream"
        )
        [Console]::Out.Write($Line)
        return
    }

    # A child that exited after accepting the write would otherwise only be
    # noticed on the *next* Event — and a Run whose last Event is the one that
    # vanished would never report it at all.
    if ($Script:TuiProcess.HasExited) {
        Invoke-GitLoopyTuiFallback -Reason (
            "the $Script:TuiCommandName helper exited"
        )
    }
}

# Run-end teardown. Closing stdin is the child's cue to draw its final frame,
# restore the terminal, and exit; the grace period bounds how long a Run waits
# for a child that does not take the cue. The Run's own exit code is never
# touched here, which is why nothing this function does can throw.
function Stop-GitLoopyTuiSession {
    [CmdletBinding()]
    param(
        [double]$GraceSeconds = -1
    )

    if ($GraceSeconds -lt 0) {
        $GraceSeconds = 5
        $Configured = $env:GIT_LOOPY_TUI_GRACE_SECONDS
        [double]$Parsed = 0
        if (
            -not [string]::IsNullOrWhiteSpace($Configured) -and
            [double]::TryParse(
                $Configured,
                [Globalization.NumberStyles]::AllowDecimalPoint,
                [Globalization.CultureInfo]::InvariantCulture,
                [ref]$Parsed
            ) -and
            [double]::IsFinite($Parsed) -and
            $Parsed -ge 0
        ) {
            $GraceSeconds = $Parsed
        }
    }

    $Script:TuiActive = $false
    if ($null -eq $Script:TuiProcess) {
        return
    }

    try {
        Close-GitLoopyTuiInput
        if (-not $Script:TuiProcess.WaitForExit([int]($GraceSeconds * 1000))) {
            $Script:TuiProcess.Kill($true)
            [void]$Script:TuiProcess.WaitForExit(1000)
        }
    }
    catch {
        # Teardown is best effort: a helper that cannot be reaped is a
        # presentation problem, and presentation never fails a Run.
    }
    finally {
        try {
            $Script:TuiProcess.Dispose()
        }
        catch {
            # Already disposed.
        }
        $Script:TuiProcess = $null
    }
}

# The single entry point a Run uses: resolve the intent, and when it is `on`,
# earn the live interface by discovering and probing a helper. Every unfulfilled
# outcome leaves the stdout sink exactly as it was, so the caller has nothing to
# undo. An explicit request that cannot be met says why; an auto-detected miss
# stays one line, because the operator never asked for anything.
function Start-GitLoopyTuiSession {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$Flag,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$EnvironmentValue,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$ReleaseVersion,
        [Parameter(Mandatory)]
        [int]$SchemaVersion,
        [AllowNull()]
        [Nullable[bool]]$IsTty
    )

    if ($null -eq $IsTty) {
        $IsTty = -not [Console]::IsOutputRedirected
    }
    $Resolved = Resolve-GitLoopyTuiIntent `
        -Flag $Flag `
        -EnvironmentValue $EnvironmentValue `
        -IsTty ([bool]$IsTty)
    if ($Resolved.Intent -cne "on") {
        return $false
    }
    $Explicit = $Resolved.Source -ceq "explicit"

    $Discovered = Find-GitLoopyTuiHelper -RepoRoot $RepoRoot
    if ($null -eq $Discovered) {
        if ($Explicit) {
            Write-GitLoopyTuiWarning (
                "interactive mode was requested but no " +
                "$Script:TuiCommandName helper was found in " +
                "$Script:TuiCloneRelativeDirectory/$Script:TuiCommandName or " +
                "on PATH; continuing with raw JSONL output on stdout"
            )
        }
        else {
            Write-GitLoopyTuiWarning (
                "no $Script:TuiCommandName helper found; using plain output"
            )
        }
        return $false
    }

    $HelperVersion = Test-GitLoopyTuiSchemaSupport `
        -Helper $Discovered.Path `
        -SchemaVersion $SchemaVersion
    if ($null -eq $HelperVersion) {
        if ($Explicit) {
            Write-GitLoopyTuiWarning (
                "interactive mode was requested but $($Discovered.Path) does " +
                "not support Event schema $SchemaVersion; continuing with " +
                "raw JSONL output on stdout"
            )
        }
        else {
            Write-GitLoopyTuiWarning (
                "$Script:TuiCommandName is not schema-compatible; using " +
                "plain output"
            )
        }
        return $false
    }

    $Identity = Test-GitLoopyTuiReleaseIdentity `
        -Source $Discovered.Source `
        -HelperVersion $HelperVersion `
        -ReleaseVersion $ReleaseVersion
    if (-not $Identity.Trusted) {
        Write-GitLoopyTuiWarning (
            "the pinned $($Discovered.Path) reports Release version " +
            "$HelperVersion, not $ReleaseVersion; reinstall it to match this " +
            "clone. Continuing with raw JSONL output on stdout"
        )
        return $false
    }
    if ($null -ne $Identity.Warning) {
        Write-GitLoopyTuiWarning $Identity.Warning
    }

    if (-not (Start-GitLoopyTuiProcess -Helper $Discovered.Path)) {
        Write-GitLoopyTuiWarning (
            "the $Script:TuiCommandName helper could not be started; " +
            "continuing with raw JSONL output on stdout"
        )
        return $false
    }
    return $true
}

Export-ModuleMember -Function @(
    "Resolve-GitLoopyTuiIntent",
    "Find-GitLoopyTuiHelper",
    "Test-GitLoopyTuiSchemaSupport",
    "Test-GitLoopyTuiReleaseIdentity",
    "Start-GitLoopyTuiSession",
    "Write-GitLoopyTuiLine",
    "Stop-GitLoopyTuiSession"
)