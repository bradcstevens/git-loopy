Set-StrictMode -Version Latest

# Staging the shared TUI helper for the PowerShell distribution (PRD #173,
# issue #194).
#
# `GitLoopy.Tui.psm1` is the *supervision* seam: it finds a helper it is allowed
# to trust and drives it. This module is the *installation* seam that puts one
# there. They meet at exactly one directory — `.git-loopy/bin/`, the clone-local
# rank-1 discovery slot — and at exactly one rule: a helper the distribution
# stages must be this Release's, proven before it is activated.
#
# Nothing here ever runs during a Run. A Run never downloads or updates software;
# installation is a separate, explicit act by the operator.
#
# The whole module keeps one promise: a helper only becomes visible once it has
# proven itself, so an interrupted or failed installation can never be the reason
# a Run loses a working live interface. Every check happens against a scratch
# copy, and the destination is touched exactly once, by a rename.

# The clone-local directory `GitLoopy.Tui.psm1` discovers first. Stated once here
# and once there, because an installer writing anywhere else would install a
# helper nothing looks for. The *file name* inside it is not a constant: Windows
# names an executable by its extension, so the pinned artefact is
# `git-loopy-tui.exe` there and `git-loopy-tui` elsewhere — whichever suffix the
# Release published for the selected target.
$Script:TuiInstallRelativeDirectory = ".git-loopy/bin"
$Script:TuiStagingPrefix = ".git-loopy-tui-staging."

function New-GitLoopyTuiInstallError {
    param(
        [Parameter(Mandatory)]
        [string]$Message
    )

    return [InvalidOperationException]::new($Message)
}

function Read-GitLoopyTuiMetadata {
    param(
        [Parameter(Mandatory)]
        [string]$Metadata
    )

    try {
        $Text = Get-Content -LiteralPath $Metadata -Raw -ErrorAction Stop
        $Parsed = $Text | ConvertFrom-Json -AsHashtable
    }
    catch {
        throw (New-GitLoopyTuiInstallError -Message (
                "cannot read helper artifact metadata $Metadata"
            ))
    }
    if ($Parsed -isnot [Collections.IDictionary]) {
        throw (New-GitLoopyTuiInstallError -Message (
                "cannot read helper artifact metadata $Metadata"
            ))
    }
    return $Parsed
}

function Get-GitLoopyTuiCommandName {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Meta
    )

    $Name = $Meta["command_name"]
    if ($Name -is [string] -and -not [string]::IsNullOrEmpty($Name)) {
        return $Name
    }
    return "git-loopy-tui"
}

# Resolve the one artifact a host with this shape should install.
#
# `System` and `Machine` are whatever the host calls itself, in the same
# vocabulary `uname -s` / `uname -m` use, so the alias tables live in the shared
# artifact metadata and every family member normalizes identically. `Libc` is
# Linux's alone; a host that cannot say which one it has takes the statically
# linked musl build, because that one runs either way.
#
# A deferred platform is named by its own recorded reason rather than by the same
# "nothing published" sentence a typo would produce.
function Select-GitLoopyTuiTarget {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Metadata,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$System,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$Machine,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$Libc
    )

    $Meta = Read-GitLoopyTuiMetadata -Metadata $Metadata
    $Command = Get-GitLoopyTuiCommandName -Meta $Meta

    $SystemKey = if ($null -eq $System) { "" } else { $System.Trim().ToLowerInvariant() }
    $MachineKey = if ($null -eq $Machine) { "" } else { $Machine.Trim().ToLowerInvariant() }
    $Aliases = $Meta["host_aliases"]
    $Os = $Aliases["systems"][$SystemKey]
    $Arch = $Aliases["machines"][$MachineKey]

    foreach ($Deferred in @($Meta["deferred_targets"])) {
        if ($Deferred["os"] -ceq $Os -and $Deferred["arch"] -ceq $Arch) {
            throw (New-GitLoopyTuiInstallError -Message (
                    "no $Command artifact for $System ${Machine}: $($Deferred["reason"])"
                ))
        }
    }

    $Candidates = @(
        @($Meta["targets"]) | Where-Object {
            $_["os"] -ceq $Os -and $_["arch"] -ceq $Arch
        }
    )
    if ($Candidates.Count -eq 0) {
        throw (New-GitLoopyTuiInstallError -Message (
                "no $Command artifact is published for $System $Machine"
            ))
    }
    if ($Candidates.Count -eq 1) {
        return [string]$Candidates[0]["triple"]
    }

    $Wanted = if ([string]::IsNullOrWhiteSpace($Libc)) {
        "musl"
    }
    else {
        $Libc.Trim().ToLowerInvariant()
    }
    foreach ($Candidate in $Candidates) {
        if ($Candidate["libc"] -ceq $Wanted) {
            return [string]$Candidate["triple"]
        }
    }
    throw (New-GitLoopyTuiInstallError -Message (
            "no $Command artifact is published for $System $Machine against $Libc"
        ))
}

# The archive, its checksum manifest, and the executable inside it. Every name
# comes from the shared templates rather than from concatenation here, so a
# Release that renames an artifact moves all of its consumers at once instead of
# leaving this one guessing.
function Get-GitLoopyTuiArtifactName {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Metadata,
        [Parameter(Mandatory)]
        [string]$Triple
    )

    $Meta = Read-GitLoopyTuiMetadata -Metadata $Metadata
    $Command = Get-GitLoopyTuiCommandName -Meta $Meta

    $Target = @($Meta["targets"]) | Where-Object { $_["triple"] -ceq $Triple } |
        Select-Object -First 1
    $Format = if ($null -eq $Target) { $null } else { $Meta["archive_formats"][$Target["os"]] }
    if ($null -eq $Target -or $null -eq $Format) {
        throw (New-GitLoopyTuiInstallError -Message (
                "helper artifact metadata $Metadata publishes no artifact for $Triple"
            ))
    }

    $Archive = ([string]$Meta["archive_name_template"]).
        Replace("{command}", $Command).
        Replace("{target}", [string]$Target["triple"]).
        Replace("{extension}", [string]$Format["extension"])
    return [pscustomobject]@{
        PSTypeName = "GitLoopy.TuiArtifactName"
        Archive = $Archive
        Checksum = ([string]$Meta["checksum_name_template"]).Replace("{archive}", $Archive)
        Executable = $Command + [string]$Format["executable_suffix"]
    }
}

# Where one Release publishes one artifact. The template is shared so the
# installers, the Homebrew formula, and the winget/Scoop manifests all resolve
# the same URL for the same Release rather than each hard-coding a guess.
function Get-GitLoopyTuiArtifactUrl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Metadata,
        [Parameter(Mandatory)]
        [string]$ReleaseVersion,
        [Parameter(Mandatory)]
        [string]$Artifact
    )

    $Meta = Read-GitLoopyTuiMetadata -Metadata $Metadata
    $Template = $Meta["release_download_url_template"]
    if ($Template -isnot [string] -or [string]::IsNullOrEmpty($Template)) {
        throw (New-GitLoopyTuiInstallError -Message (
                "helper artifact metadata $Metadata declares no release download URL"
            ))
    }
    return $Template.Replace("{version}", $ReleaseVersion).Replace("{artifact}", $Artifact)
}

function Get-GitLoopyTuiDigest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    try {
        $Hash = Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop
    }
    catch {
        throw (New-GitLoopyTuiInstallError -Message "cannot read release artifact $Path")
    }
    return $Hash.Hash.ToLowerInvariant()
}

# Prove `Archive` is the artifact its published checksum names, and return the
# digest that proved it.
#
# Both halves are checked. A digest that matches proves nothing if it was
# published for a different file, so the manifest's filename has to be this
# artifact's too — otherwise a correct macOS arm64 checksum would happily bless
# the x64 archive an installer downloaded by mistake.
function Test-GitLoopyTuiChecksum {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Archive,
        [Parameter(Mandatory)]
        [string]$Manifest
    )

    try {
        $Lines = @(Get-Content -LiteralPath $Manifest -ErrorAction Stop)
    }
    catch {
        throw (New-GitLoopyTuiInstallError -Message "cannot read checksum manifest $Manifest")
    }

    $ArchiveName = [IO.Path]::GetFileName($Archive)
    $Published = [Collections.Generic.List[string]]::new()
    $Expected = ""
    foreach ($Line in $Lines) {
        $Fields = @(
            $Line -split "\s+" | Where-Object { -not [string]::IsNullOrEmpty($_) }
        )
        if ($Fields.Count -lt 2) {
            continue
        }
        if ($Fields[0] -notmatch "^[0-9a-fA-F]{64}$") {
            continue
        }
        # A `*` prefix is how the coreutils tools mark a binary-mode entry; it is
        # part of the mode, not of the filename.
        $Name = $Fields[1].TrimStart("*")
        $Published.Add($Name)
        if ($Name -ceq $ArchiveName) {
            $Expected = $Fields[0].ToLowerInvariant()
        }
    }

    if ($Published.Count -eq 0) {
        throw (New-GitLoopyTuiInstallError -Message (
                "checksum manifest $Manifest declares no SHA-256 entry"
            ))
    }
    if ([string]::IsNullOrEmpty($Expected)) {
        throw (New-GitLoopyTuiInstallError -Message (
                "checksum manifest $Manifest publishes $($Published -join " ") " +
                "rather than $ArchiveName"
            ))
    }

    $Actual = Get-GitLoopyTuiDigest -Path $Archive
    if ($Actual -cne $Expected) {
        throw (New-GitLoopyTuiInstallError -Message (
                "release artifact $ArchiveName failed its SHA-256 checksum: " +
                "expected $Expected, computed $Actual"
            ))
    }
    return $Actual
}

# The execute bit, where the operating system has one. `SetUnixFileMode` arrived
# in .NET 7, so a PowerShell 7.0-7.2 host falls back to `chmod` rather than
# staging a helper it has just made unrunnable.
function Set-GitLoopyTuiExecutable {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    if ($IsWindows) {
        return
    }
    $Executable = (
        [IO.UnixFileMode]::UserExecute -bor
        [IO.UnixFileMode]::GroupExecute -bor
        [IO.UnixFileMode]::OtherExecute
    )
    try {
        [IO.File]::SetUnixFileMode(
            $Path, ([IO.File]::GetUnixFileMode($Path) -bor $Executable)
        )
        return
    }
    catch [Management.Automation.MethodException] {
        & chmod +x $Path
        if ($LASTEXITCODE -ne 0) {
            throw (New-GitLoopyTuiInstallError -Message "cannot make $Path executable")
        }
        return
    }
    catch {
        throw (New-GitLoopyTuiInstallError -Message "cannot make $Path executable")
    }
}

# Unpack `Archive` into `Destination` and return the path of the one executable
# the Release published inside it.
#
# The executable is looked up *by its published name* rather than by taking
# whatever the archive happens to contain: an archive is attacker-shaped input
# until its checksum has been verified, and even a well-formed one that carries a
# differently named binary is not the artifact this Release meant to ship.
function Expand-GitLoopyTuiArchive {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Archive,
        [Parameter(Mandatory)]
        [string]$Destination,
        [Parameter(Mandatory)]
        [string]$ExecutableName
    )

    $ArchiveName = [IO.Path]::GetFileName($Archive)
    try {
        [void](New-Item -ItemType Directory -Force -Path $Destination -ErrorAction Stop)
    }
    catch {
        throw (New-GitLoopyTuiInstallError -Message "cannot create $Destination")
    }

    if ($Archive.EndsWith(".zip", [StringComparison]::OrdinalIgnoreCase)) {
        try {
            Expand-Archive -LiteralPath $Archive -DestinationPath $Destination `
                -Force -ErrorAction Stop
        }
        catch {
            throw (New-GitLoopyTuiInstallError -Message "cannot unpack $ArchiveName")
        }
    }
    else {
        # `-xf` rather than `-xJf`: GNU tar and libarchive both detect xz from the
        # stream, and letting them do it keeps one code path for any compression a
        # later Release picks.
        & tar -xf $Archive -C $Destination 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw (New-GitLoopyTuiInstallError -Message "cannot unpack $ArchiveName")
        }
    }

    # cargo-dist archives the executable at the root, but a Release that starts
    # wrapping it in a versioned directory should install rather than fail, so the
    # search is by name over the whole extraction.
    $Found = @(
        Get-ChildItem -LiteralPath $Destination -Recurse -File -Force `
            -Filter $ExecutableName -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ceq $ExecutableName } |
            Sort-Object -Property FullName -CaseSensitive
    )
    if ($Found.Count -eq 0) {
        throw (New-GitLoopyTuiInstallError -Message (
                "$ArchiveName does not contain $ExecutableName"
            ))
    }

    Set-GitLoopyTuiExecutable -Path $Found[0].FullName
    return $Found[0].FullName
}

# Run one staged helper with one argument and return its stdout, or `$null` when
# it could not answer at all. Nothing here inherits a console: the helper is
# untrusted until both probes pass, so it never gets to touch the terminal.
function Invoke-GitLoopyTuiProbe {
    param(
        [Parameter(Mandatory)]
        [string]$Helper,
        [Parameter(Mandatory)]
        [string]$Argument
    )

    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Helper
    $StartInfo.ArgumentList.Add($Argument)
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true

    try {
        $Process = [Diagnostics.Process]::new()
        try {
            $Process.StartInfo = $StartInfo
            if (-not $Process.Start()) {
                return $null
            }
            $Output = $Process.StandardOutput.ReadToEnd()
            [void]$Process.StandardError.ReadToEnd()
            $Process.WaitForExit()
            if ($Process.ExitCode -ne 0) {
                return $null
            }
            return $Output
        }
        finally {
            $Process.Dispose()
        }
    }
    catch {
        return $null
    }
}

# The gate a staged helper passes before it becomes the active one.
#
# Two separate questions, asked in the order that makes a failure legible. Is it
# this Release — Wrapper contract §16 requires exact equality for a component
# selected as an artifact of *this* distribution, and fails closed on drift. And
# can it decode what this Orchestrator emits — asked as containment rather than
# equality, because a later helper may decode a range of Event schemas at once.
#
# Release equality never answers the second question. A helper that reports this
# Release and probes as another one is refused: identity is what `--version`
# says, and a helper that contradicts itself has established neither.
function Test-GitLoopyTuiStagedHelper {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Helper,
        [Parameter(Mandatory)]
        [string]$ReleaseVersion,
        [Parameter(Mandatory)]
        [int]$SchemaVersion,
        [string]$CommandName = "git-loopy-tui"
    )

    $Reported = Invoke-GitLoopyTuiProbe -Helper $Helper -Argument "--version"
    if ($null -eq $Reported) {
        throw (New-GitLoopyTuiInstallError -Message (
                "the staged helper could not answer --version"
            ))
    }
    $Reported = $Reported.Trim()
    $Expected = "$CommandName $ReleaseVersion"
    if ($Reported -cne $Expected) {
        throw (New-GitLoopyTuiInstallError -Message (
                "the staged helper reports '$Reported', not '$Expected'"
            ))
    }

    $Refusal = New-GitLoopyTuiInstallError -Message (
        "the staged helper does not support Event schema $SchemaVersion " +
        "as Release $ReleaseVersion"
    )
    $Probe = Invoke-GitLoopyTuiProbe -Helper $Helper -Argument "--schema-version"
    if ($null -eq $Probe) {
        throw (New-GitLoopyTuiInstallError -Message (
                "the staged helper could not answer --schema-version"
            ))
    }
    try {
        $Parsed = $Probe | ConvertFrom-Json -AsHashtable
    }
    catch {
        throw $Refusal
    }
    if ($Parsed -isnot [Collections.IDictionary]) {
        throw $Refusal
    }
    foreach ($Key in @("min_event_schema_version", "max_event_schema_version")) {
        # A JSON string bound is not a bound: comparing it would make PowerShell
        # coerce the *number* to a string and order it lexicographically, so
        # `"10"` would sort below `2`.
        if ($Parsed[$Key] -isnot [int] -and $Parsed[$Key] -isnot [long] -and
            $Parsed[$Key] -isnot [double] -and $Parsed[$Key] -isnot [decimal]) {
            throw $Refusal
        }
    }
    if ($Parsed["min_event_schema_version"] -gt $SchemaVersion -or
        $Parsed["max_event_schema_version"] -lt $SchemaVersion) {
        throw $Refusal
    }
    if ($Parsed["version"] -is [string] -and $Parsed["version"] -cne $ReleaseVersion) {
        throw $Refusal
    }
}

# A scratch directory beside the destination, and the reason the whole module can
# promise a failed installation costs nothing.
#
# It is a *sibling* rather than a temporary directory so that activation is a
# same-directory rename. A rename across filesystems is a copy, and a copy can be
# interrupted half-written — which would leave a truncated file sitting in exactly
# the slot `GitLoopy.Tui.psm1` discovers first. The directory is hidden and
# prefixed so an abandoned one is recognisable as this installer's.
function New-GitLoopyTuiWorkspace {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Destination
    )

    $Parent = Split-Path -Parent $Destination
    try {
        [void](New-Item -ItemType Directory -Force -Path $Parent -ErrorAction Stop)
        $Workspace = Join-Path $Parent (
            $Script:TuiStagingPrefix + [IO.Path]::GetRandomFileName()
        )
        [void](New-Item -ItemType Directory -Path $Workspace -ErrorAction Stop)
    }
    catch {
        throw (New-GitLoopyTuiInstallError -Message "cannot stage a helper under $Parent")
    }
    return $Workspace
}

# The one operation that changes what a Run will discover. Everything before it is
# reversible by deleting a scratch directory; this is not, which is why it happens
# last and happens once.
function Move-GitLoopyTuiHelper {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Verified,
        [Parameter(Mandatory)]
        [string]$Destination
    )

    Set-GitLoopyTuiExecutable -Path $Verified
    try {
        [IO.File]::Move($Verified, $Destination, $true)
    }
    catch {
        throw (New-GitLoopyTuiInstallError -Message (
                "cannot install the verified helper to $Destination"
            ))
    }
}

# Which C library a Linux host links against — the one selection input the
# runtime cannot answer, and the reason two Linux artifacts exist per
# architecture.
#
# Parsed from `ldd --version` text rather than inferred from the distribution,
# because the answer that matters is what this binary will be asked to link
# against, not what the packaging says. An unrecognized answer is left empty on
# purpose: `Select-GitLoopyTuiTarget` then takes the statically linked musl build,
# which runs either way, instead of guessing at a dynamic one.
function ConvertFrom-GitLoopyLddReport {
    [CmdletBinding()]
    param(
        [AllowNull()]
        [AllowEmptyString()]
        [string]$Report
    )

    $Lowered = if ($null -eq $Report) { "" } else { $Report.ToLowerInvariant() }
    if ($Lowered.Contains("musl")) {
        return "musl"
    }
    foreach ($Marker in @("glibc", "gnu libc", "gnu c library")) {
        if ($Lowered.Contains($Marker)) {
            return "gnu"
        }
    }
    return ""
}

function Get-GitLoopyTuiHostLibc {
    [CmdletBinding()]
    param()

    if (-not $IsLinux) {
        return ""
    }
    $Report = try {
        (& ldd --version 2>&1 | Out-String)
    }
    catch {
        ""
    }
    return ConvertFrom-GitLoopyLddReport -Report $Report
}

# What this host calls itself, in the vocabulary the shared alias tables speak.
# The runtime answers rather than `uname`, because Windows has no `uname` and this
# is the family member that runs there.
function Get-GitLoopyTuiHostShape {
    [CmdletBinding()]
    param()

    $System = if ($IsWindows) { "Windows" }
    elseif ($IsMacOS) { "Darwin" }
    elseif ($IsLinux) { "Linux" }
    else { "" }
    return [pscustomobject]@{
        PSTypeName = "GitLoopy.TuiHostShape"
        System = $System
        Machine = [string][Runtime.InteropServices.RuntimeInformation]::OSArchitecture
        Libc = Get-GitLoopyTuiHostLibc
    }
}

function Get-GitLoopyTuiHostTarget {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Metadata
    )

    $Shape = Get-GitLoopyTuiHostShape
    return Select-GitLoopyTuiTarget -Metadata $Metadata -System $Shape.System `
        -Machine $Shape.Machine -Libc $Shape.Libc
}

# Fetch one published file.
#
# `Invoke-WebRequest` throws on any status that is not success, which is what
# turns an HTTP error page into a failed download rather than into an "archive"
# that later fails to unpack for a reason that says nothing about what went
# wrong. It implements no `file:` scheme, though, and the shell installer's curl
# does — so the scheme is dispatched here rather than leaving the two installers
# accepting different `--tui-base-url` values.
function Save-GitLoopyTuiArtifact {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Uri,
        [Parameter(Mandatory)]
        [string]$Destination
    )

    $Parsed = $null
    if (-not [Uri]::TryCreate($Uri, [UriKind]::Absolute, [ref]$Parsed)) {
        throw (New-GitLoopyTuiInstallError -Message "cannot download $Uri")
    }
    if ($Parsed.Scheme -ceq "file") {
        try {
            [IO.File]::Copy($Parsed.LocalPath, $Destination, $true)
        }
        catch {
            throw (New-GitLoopyTuiInstallError -Message "cannot download $Uri")
        }
        return
    }

    $Progress = $ProgressPreference
    try {
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest -Uri $Uri -OutFile $Destination -ErrorAction Stop
    }
    catch {
        throw (New-GitLoopyTuiInstallError -Message "cannot download $Uri")
    }
    finally {
        $ProgressPreference = $Progress
    }
}

# Install one verified helper into `<RepositoryRoot>/.git-loopy/bin/`.
#
# The order is the whole contract, and it is the order a failure is cheapest in:
# pick the artifact this host publishes, obtain it and its published checksum,
# prove the checksum over both filename and digest, unpack it, prove it reports
# this clone's Release and decodes this Orchestrator's Event schema — and only
# then rename it into the slot a Run discovers. Everything before the rename
# happens inside a scratch directory that is removed either way, so a prior
# verified helper survives every failure above untouched.
#
# `Archive` / `Checksum` are the air-gapped path: a host with no network hands
# over files it already has, and they face exactly the same proofs as a download.
function Install-GitLoopyTuiHelper {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Metadata,
        [Parameter(Mandatory)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory)]
        [string]$ReleaseVersion,
        [Parameter(Mandatory)]
        [int]$SchemaVersion,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$BaseUrl,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$Archive,
        [AllowNull()]
        [AllowEmptyString()]
        [string]$Checksum
    )

    $Meta = Read-GitLoopyTuiMetadata -Metadata $Metadata
    $Command = Get-GitLoopyTuiCommandName -Meta $Meta

    $Triple = Get-GitLoopyTuiHostTarget -Metadata $Metadata
    $Names = Get-GitLoopyTuiArtifactName -Metadata $Metadata -Triple $Triple

    $Destination = Join-Path (
        Join-Path $RepositoryRoot $Script:TuiInstallRelativeDirectory
    ) $Names.Executable
    $Workspace = New-GitLoopyTuiWorkspace -Destination $Destination
    try {
        $StagedArchive = Join-Path $Workspace $Names.Archive
        $StagedChecksum = Join-Path $Workspace $Names.Checksum

        if (-not [string]::IsNullOrEmpty($Archive)) {
            if ([string]::IsNullOrEmpty($Checksum)) {
                throw (New-GitLoopyTuiInstallError -Message (
                        "-TuiArchive needs the artifact's published checksum " +
                        "manifest; pass -TuiChecksum too"
                    ))
            }
            foreach ($Pair in @(
                    @($Archive, $StagedArchive), @($Checksum, $StagedChecksum)
                )) {
                try {
                    [IO.File]::Copy($Pair[0], $Pair[1], $true)
                }
                catch {
                    throw (New-GitLoopyTuiInstallError -Message "cannot read $($Pair[0])")
                }
            }
        }
        else {
            $ArchiveUrl, $ChecksumUrl = if (-not [string]::IsNullOrEmpty($BaseUrl)) {
                $Trimmed = $BaseUrl.TrimEnd("/")
                "$Trimmed/$($Names.Archive)", "$Trimmed/$($Names.Checksum)"
            }
            else {
                (Get-GitLoopyTuiArtifactUrl -Metadata $Metadata `
                    -ReleaseVersion $ReleaseVersion -Artifact $Names.Archive),
                (Get-GitLoopyTuiArtifactUrl -Metadata $Metadata `
                    -ReleaseVersion $ReleaseVersion -Artifact $Names.Checksum)
            }
            Save-GitLoopyTuiArtifact -Uri $ArchiveUrl -Destination $StagedArchive
            Save-GitLoopyTuiArtifact -Uri $ChecksumUrl -Destination $StagedChecksum
        }

        [void](Test-GitLoopyTuiChecksum -Archive $StagedArchive -Manifest $StagedChecksum)
        $Staged = Expand-GitLoopyTuiArchive -Archive $StagedArchive `
            -Destination (Join-Path $Workspace "unpacked") `
            -ExecutableName $Names.Executable
        Test-GitLoopyTuiStagedHelper -Helper $Staged -ReleaseVersion $ReleaseVersion `
            -SchemaVersion $SchemaVersion -CommandName $Command
        Move-GitLoopyTuiHelper -Verified $Staged -Destination $Destination
    }
    finally {
        # The workspace is a sibling of the destination, so it would otherwise be
        # discoverable debris in the directory a Run searches.
        Remove-Item -LiteralPath $Workspace -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $Destination
}

Export-ModuleMember -Function @(
    "Select-GitLoopyTuiTarget",
    "Get-GitLoopyTuiArtifactName",
    "Get-GitLoopyTuiArtifactUrl",
    "Get-GitLoopyTuiDigest",
    "Test-GitLoopyTuiChecksum",
    "Expand-GitLoopyTuiArchive",
    "Test-GitLoopyTuiStagedHelper",
    "New-GitLoopyTuiWorkspace",
    "Move-GitLoopyTuiHelper",
    "ConvertFrom-GitLoopyLddReport",
    "Get-GitLoopyTuiHostLibc",
    "Get-GitLoopyTuiHostShape",
    "Get-GitLoopyTuiHostTarget",
    "Save-GitLoopyTuiArtifact",
    "Install-GitLoopyTuiHelper"
)
