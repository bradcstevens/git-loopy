Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# The PowerShell installer's helper-staging suite (PRD #173, issue #194).
#
# Every case here is network-free. The download path is still a *real* transfer:
# the "published Release" is served over HTTP by a loopback `TcpListener` this
# suite starts, so `Invoke-WebRequest` is exercised against a real socket and a
# real status line rather than stubbed out.
#
# Windows cannot execute a fabricated helper — a file named `git-loopy-tui.exe`
# has to be a real PE image, and nothing here can compile one — so the cases that
# must *run* a staged helper are Unix-only and say so. Everything that does not
# execute the helper, including the `.zip` extraction branch that only Windows
# selects, runs on every platform.

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7+ is required (found $($PSVersionTable.PSVersion))."
}

$PortDir = Split-Path -Parent $PSScriptRoot
$ConformanceDir = Join-Path (Split-Path -Parent $PortDir) "conformance"
$ArtifactMetadata = Join-Path $ConformanceDir "tui-artifacts.json"

Import-Module (Join-Path $PortDir "GitLoopy.Events.psm1") -Force
Import-Module (Join-Path $PortDir "GitLoopy.Tui.psm1") -Force
Import-Module (Join-Path $PortDir "GitLoopy.TuiInstall.psm1") -Force

$SchemaVersion = Get-GitLoopyEventSchemaVersion
$CanRunFabricatedHelper = -not $IsWindows

function Assert-True {
    param(
        [Parameter(Mandatory)]
        [bool]$Condition,
        [Parameter(Mandatory)]
        [string]$Description
    )

    if (-not $Condition) {
        throw "FAIL: $Description"
    }
}

function Assert-Equal {
    param(
        [AllowNull()]
        [object]$Expected,
        [AllowNull()]
        [object]$Actual,
        [Parameter(Mandatory)]
        [string]$Description
    )

    if ($Expected -is [string] -and $Actual -is [string]) {
        if ($Expected -cne $Actual) {
            throw "FAIL: $Description`nexpected: $Expected`nactual:   $Actual"
        }
        return
    }
    if ($Expected -ne $Actual) {
        throw "FAIL: $Description`nexpected: $Expected`nactual:   $Actual"
    }
}

function Assert-Contains {
    param(
        [AllowNull()]
        [AllowEmptyString()]
        [string]$Haystack,
        [Parameter(Mandatory)]
        [string]$Needle,
        [Parameter(Mandatory)]
        [string]$Description
    )

    if ($null -eq $Haystack -or -not $Haystack.Contains($Needle)) {
        throw "FAIL: $Description`nexpected to contain: $Needle`nactual: $Haystack"
    }
}

# The message of the exception a scriptblock throws, or the empty string when it
# does not throw at all.
function Get-RefusalMessage {
    param(
        [Parameter(Mandatory)]
        [scriptblock]$Action
    )

    try {
        & $Action | Out-Null
    }
    catch {
        return [string]$_.Exception.Message
    }
    return ""
}

$Workspaces = [Collections.Generic.List[string]]::new()

function New-Scratch {
    param(
        [Parameter(Mandatory)]
        [string]$Name
    )

    $Path = Join-Path ([IO.Path]::GetTempPath()) (
        "git-loopy-$Name-" + [IO.Path]::GetRandomFileName()
    )
    [void](New-Item -ItemType Directory -Path $Path -Force)
    $Full = (Resolve-Path -LiteralPath $Path).Path
    $Workspaces.Add($Full)
    return $Full
}

# --- Target selection -------------------------------------------------------
#
# The shared fixture's `selection_cases` are the same oracle the Python consumer
# and the shell installer read, so a host shape that resolves differently in the
# three of them is a fixture failure rather than a difference of opinion.

$Metadata = Get-Content -LiteralPath $ArtifactMetadata -Raw | ConvertFrom-Json -AsHashtable
foreach ($Case in @($Metadata["selection_cases"])) {
    $Libc = if ($null -eq $Case["libc"]) { "" } else { [string]$Case["libc"] }
    $Actual = ""
    $Refusal = ""
    try {
        $Actual = Select-GitLoopyTuiTarget -Metadata $ArtifactMetadata `
            -System $Case["system"] -Machine $Case["machine"] -Libc $Libc
    }
    catch {
        $Refusal = [string]$_.Exception.Message
    }

    $ExpectedTarget = if ($null -eq $Case["target"]) { "" } else { [string]$Case["target"] }
    Assert-Equal $ExpectedTarget $Actual "selection fixture target: $($Case["id"])"
    if ($null -ne $Case["error"]) {
        Assert-Contains $Refusal ([string]$Case["error"]) (
            "selection fixture error: $($Case["id"])")
    }
}

# --- Artifact naming --------------------------------------------------------
#
# Expected names are written out rather than re-derived from the templates, so a
# template edit has to be a deliberate change to this file too.

$MacNames = Get-GitLoopyTuiArtifactName -Metadata $ArtifactMetadata `
    -Triple aarch64-apple-darwin
Assert-Equal "git-loopy-tui-aarch64-apple-darwin.tar.xz" $MacNames.Archive `
    "macOS arm64 archive name"
Assert-Equal "git-loopy-tui-aarch64-apple-darwin.tar.xz.sha256" $MacNames.Checksum `
    "macOS arm64 checksum name"
Assert-Equal "git-loopy-tui" $MacNames.Executable "macOS arm64 executable name"

$WindowsNames = Get-GitLoopyTuiArtifactName -Metadata $ArtifactMetadata `
    -Triple x86_64-pc-windows-msvc
Assert-Equal "git-loopy-tui-x86_64-pc-windows-msvc.zip" $WindowsNames.Archive `
    "Windows x64 archive name"
Assert-Equal "git-loopy-tui-x86_64-pc-windows-msvc.zip.sha256" $WindowsNames.Checksum `
    "Windows x64 checksum name"
# Windows names an executable by extension, so this is also the file name the
# clone-local discovery slot has to carry there.
Assert-Equal "git-loopy-tui.exe" $WindowsNames.Executable "Windows x64 executable name"

$MuslNames = Get-GitLoopyTuiArtifactName -Metadata $ArtifactMetadata `
    -Triple x86_64-unknown-linux-musl
Assert-Equal "git-loopy-tui-x86_64-unknown-linux-musl.tar.xz" $MuslNames.Archive `
    "musl Linux x64 archive name"

Assert-Contains (Get-RefusalMessage {
        Get-GitLoopyTuiArtifactName -Metadata $ArtifactMetadata -Triple sparc-unknown-none
    }) "publishes no artifact" "a triple the Release does not publish is refused"

# --- Download URL -----------------------------------------------------------

Assert-Equal (
    "https://github.com/bradcstevens/git-loopy/releases/download/v9.9.9/" +
    "git-loopy-tui-aarch64-apple-darwin.tar.xz"
) (
    Get-GitLoopyTuiArtifactUrl -Metadata $ArtifactMetadata -ReleaseVersion 9.9.9 `
        -Artifact git-loopy-tui-aarch64-apple-darwin.tar.xz
) "artifact URL resolves against the published Release tag"

# --- Checksum verification --------------------------------------------------
#
# Both halves are load-bearing. A digest that matches proves nothing if it was
# published for a different file, so the manifest has to name *this* artifact
# too — otherwise a correct macOS arm64 checksum would happily bless the x64
# archive an installer downloaded by mistake.

$ChecksumDir = New-Scratch -Name checksum
$Artifact = Join-Path $ChecksumDir "git-loopy-tui-x86_64-apple-darwin.tar.xz"
[IO.File]::WriteAllBytes($Artifact, [Text.Encoding]::ASCII.GetBytes("helper"))
# The SHA-256 of the six bytes `helper`, computed independently of this suite.
$HelperDigest = "e81d3b0e9d82feaaf5f6e55bdff24731d7eee08632ffa63801e6397290c5d20a"
Assert-Equal $HelperDigest (Get-GitLoopyTuiDigest -Path $Artifact) `
    "SHA-256 of a known artifact"

$Manifest = "$Artifact.sha256"
[IO.File]::WriteAllText(
    $Manifest, "$HelperDigest  git-loopy-tui-x86_64-apple-darwin.tar.xz`n")
Assert-Equal $HelperDigest (
    Test-GitLoopyTuiChecksum -Archive $Artifact -Manifest $Manifest
) "a published checksum proves the artifact it names"

# The coreutils binary-mode marker is part of the mode, not of the filename.
[IO.File]::WriteAllText(
    $Manifest, "$HelperDigest *git-loopy-tui-x86_64-apple-darwin.tar.xz`n")
Assert-Equal $HelperDigest (
    Test-GitLoopyTuiChecksum -Archive $Artifact -Manifest $Manifest
) "a binary-mode manifest entry names the same artifact"

$Tampered = Join-Path $ChecksumDir "tampered.tar.xz"
[IO.File]::WriteAllBytes($Tampered, [Text.Encoding]::ASCII.GetBytes("tampered"))
[IO.File]::WriteAllText("$Tampered.sha256", "$HelperDigest  tampered.tar.xz`n")
Assert-Contains (Get-RefusalMessage {
        Test-GitLoopyTuiChecksum -Archive $Tampered -Manifest "$Tampered.sha256"
    }) "failed its SHA-256 checksum" "a tampered artifact is refused by checksum"

$Mismatched = Join-Path $ChecksumDir "mismatched.sha256"
[IO.File]::WriteAllText($Mismatched, "$HelperDigest  some-other-artifact.tar.xz`n")
Assert-Contains (Get-RefusalMessage {
        Test-GitLoopyTuiChecksum -Archive $Artifact -Manifest $Mismatched
    }) "some-other-artifact.tar.xz" `
    "a checksum for another artifact names what it actually publishes"

$Empty = Join-Path $ChecksumDir "empty.sha256"
[IO.File]::WriteAllText($Empty, "not a checksum manifest`n")
Assert-Contains (Get-RefusalMessage {
        Test-GitLoopyTuiChecksum -Archive $Artifact -Manifest $Empty
    }) "declares no SHA-256 entry" "a manifest with no SHA-256 entry is refused"

Assert-Contains (Get-RefusalMessage {
        Test-GitLoopyTuiChecksum -Archive $Artifact `
            -Manifest (Join-Path $ChecksumDir "absent.sha256")
    }) "cannot read checksum manifest" "a missing checksum manifest is refused"

# --- Extraction -------------------------------------------------------------
#
# Both published archive formats. `.zip` is the branch only Windows selects, so
# it is exercised everywhere rather than left to one leg of the matrix.

$ExtractDir = New-Scratch -Name extract

function New-FakeHelperScript {
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string]$ReportedVersion,
        [int]$Minimum = 1,
        [int]$Maximum = $SchemaVersion,
        [string]$ProbedVersion = ""
    )

    if ([string]::IsNullOrEmpty($ProbedVersion)) {
        $ProbedVersion = $ReportedVersion
    }
    [void](New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path))
    $Probe = (
        '{"version": "' + $ProbedVersion + '", "min_event_schema_version": ' +
        $Minimum + ', "max_event_schema_version": ' + $Maximum + '}'
    )
    $Script = (
        "#!/usr/bin/env bash`n" +
        "case `"`${1-}`" in`n" +
        "  --version) printf 'git-loopy-tui %s\n' '$ReportedVersion' ;;`n" +
        "  --schema-version) printf '%s\n' '$Probe' ;;`n" +
        "  *) cat >/dev/null ;;`n" +
        "esac`n"
    )
    [IO.File]::WriteAllText($Path, $Script, [Text.UTF8Encoding]::new($false))
    if (-not $IsWindows) {
        & chmod +x $Path
    }
}

$Payload = Join-Path $ExtractDir "payload"
New-FakeHelperScript -Path (Join-Path $Payload "git-loopy-tui") -ReportedVersion 4.5.6

$Zip = Join-Path $ExtractDir "helper.zip"
Compress-Archive -Path (Join-Path $Payload "git-loopy-tui") -DestinationPath $Zip -Force
$StagedFromZip = Expand-GitLoopyTuiArchive -Archive $Zip `
    -Destination (Join-Path $ExtractDir "from-zip") -ExecutableName "git-loopy-tui"
Assert-True ([IO.File]::Exists($StagedFromZip)) "the zip archive yielded the helper"

Assert-Contains (Get-RefusalMessage {
        Expand-GitLoopyTuiArchive -Archive $Zip `
            -Destination (Join-Path $ExtractDir "from-zip-wrong") `
            -ExecutableName "git-loopy-tui.exe"
    }) "does not contain git-loopy-tui.exe" `
    "an archive without the published executable names what was missing"

if (-not $IsWindows) {
    $Tarball = Join-Path $ExtractDir "helper.tar.xz"
    & tar -cJf $Tarball -C $Payload "git-loopy-tui"
    Assert-Equal 0 $LASTEXITCODE "the fixture tarball was created"
    $StagedFromTar = Expand-GitLoopyTuiArchive -Archive $Tarball `
        -Destination (Join-Path $ExtractDir "from-tar") -ExecutableName "git-loopy-tui"
    Assert-Equal "git-loopy-tui 4.5.6" (
        (& $StagedFromTar --version | Out-String).Trim()
    ) "the extracted helper is the archived one, and is executable"
}

Assert-Contains (Get-RefusalMessage {
        Expand-GitLoopyTuiArchive -Archive (Join-Path $ExtractDir "absent.zip") `
            -Destination (Join-Path $ExtractDir "absent-into") `
            -ExecutableName "git-loopy-tui"
    }) "cannot unpack" "an unreadable archive is refused"

# --- Version and capability probe -------------------------------------------
#
# `--version` is Release identity and `--schema-version` is compatibility; §16 of
# the Wrapper contract keeps them separate, and a helper this distribution stages
# has to satisfy both before it is allowed to become the active one.

if ($CanRunFabricatedHelper) {
    $ProbeDir = New-Scratch -Name probe

    New-FakeHelperScript -Path (Join-Path $ProbeDir "good") -ReportedVersion 4.5.6
    Test-GitLoopyTuiStagedHelper -Helper (Join-Path $ProbeDir "good") `
        -ReleaseVersion 4.5.6 -SchemaVersion $SchemaVersion

    New-FakeHelperScript -Path (Join-Path $ProbeDir "other-release") -ReportedVersion 9.9.9
    Assert-Contains (Get-RefusalMessage {
            Test-GitLoopyTuiStagedHelper -Helper (Join-Path $ProbeDir "other-release") `
                -ReleaseVersion 4.5.6 -SchemaVersion $SchemaVersion
        }) "9.9.9" "a helper from another Release reports what it actually is"

    New-FakeHelperScript -Path (Join-Path $ProbeDir "older-schema") `
        -ReportedVersion 4.5.6 -Minimum 0 -Maximum 0
    Assert-Contains (Get-RefusalMessage {
            Test-GitLoopyTuiStagedHelper -Helper (Join-Path $ProbeDir "older-schema") `
                -ReleaseVersion 4.5.6 -SchemaVersion $SchemaVersion
        }) "Event schema" "an incompatible helper is refused by capability, not by version"

    # Release equality is product identity and never the compatibility authority
    # (§16), so a helper that claims this Release while probing as another one is
    # refused too.
    New-FakeHelperScript -Path (Join-Path $ProbeDir "lying") -ReportedVersion 4.5.6 `
        -ProbedVersion 9.9.9
    Assert-Contains (Get-RefusalMessage {
            Test-GitLoopyTuiStagedHelper -Helper (Join-Path $ProbeDir "lying") `
                -ReleaseVersion 4.5.6 -SchemaVersion $SchemaVersion
        }) "Event schema" "a helper whose probe disagrees with its --version is refused"

    $Broken = Join-Path $ProbeDir "broken"
    [IO.File]::WriteAllText($Broken, "#!/usr/bin/env bash`nexit 3`n")
    & chmod +x $Broken
    Assert-Contains (Get-RefusalMessage {
            Test-GitLoopyTuiStagedHelper -Helper $Broken -ReleaseVersion 4.5.6 `
                -SchemaVersion $SchemaVersion
        }) "could not answer --version" "a helper that cannot answer --version is refused"
}

# --- Atomic activation ------------------------------------------------------
#
# The workspace is deliberately a sibling of the destination, so the last step is
# a same-directory rename: the bytes that were verified are exactly the bytes that
# land, and there is no window in which a half-written file is discoverable as the
# clone-local helper.

$ActivateDir = New-Scratch -Name activate
# Windows names an executable by its extension, so the installer stages
# `git-loopy-tui.exe` there and that is the only name the Orchestrator's finder
# looks for. Hard-coding the POSIX name here would stage into a slot nothing
# discovers, and the discovery assertion below would be testing the wrong file.
$HelperFileName = if ($IsWindows) { "git-loopy-tui.exe" } else { "git-loopy-tui" }
$Destination = Join-Path $ActivateDir (Join-Path "repo/.git-loopy/bin" $HelperFileName)
$Workspace = New-GitLoopyTuiWorkspace -Destination $Destination
Assert-Equal (Split-Path -Parent $Destination) (Split-Path -Parent $Workspace) `
    "the staging workspace is a sibling of the destination"

[IO.File]::WriteAllText((Join-Path $Workspace "git-loopy-tui"), "fresh")
Move-GitLoopyTuiHelper -Verified (Join-Path $Workspace "git-loopy-tui") `
    -Destination $Destination
Assert-Equal "fresh" ([IO.File]::ReadAllText($Destination)) `
    "activation installs the verified helper"

[IO.File]::WriteAllText((Join-Path $Workspace "git-loopy-tui"), "upgraded")
Move-GitLoopyTuiHelper -Verified (Join-Path $Workspace "git-loopy-tui") `
    -Destination $Destination
Assert-Equal "upgraded" ([IO.File]::ReadAllText($Destination)) `
    "activation replaces the installed helper"
Remove-Item -LiteralPath $Workspace -Recurse -Force

# The installation slot and the discovery slot are the same slot, asserted rather
# than asserted-by-comment: the Orchestrator's own finder has to see what the
# installer just wrote.
if (-not $IsWindows) {
    & chmod +x $Destination
}
$Discovered = Find-GitLoopyTuiHelper -RepoRoot (Join-Path $ActivateDir "repo") -SearchPath ""
Assert-True ($null -ne $Discovered) "the Orchestrator discovers what the installer staged"
Assert-Equal "clone-local" $Discovered.Source "the staged helper is the clone-local one"

# --- Host architecture vocabulary -------------------------------------------
#
# The shared alias tables are keyed in `uname -m` vocabulary, because that is what
# the Python consumer and the shell installer feed them. This is the family member
# with no `uname`, so it asks .NET instead — and .NET answers in its own dialect
# (`X64`, `Arm64`, `Arm`), which is not the same language. The translation is
# therefore pinned here rather than left to the coincidence that two of those
# names happen to lower-case into keys the table already holds.

Assert-Equal "x86_64" (ConvertFrom-GitLoopyRuntimeArchitecture -Architecture X64) `
    "the 64-bit x86 runtime name is translated to uname's"
Assert-Equal "aarch64" (ConvertFrom-GitLoopyRuntimeArchitecture -Architecture Arm64) `
    "the 64-bit ARM runtime name is translated to uname's"
Assert-Equal "armv7l" (ConvertFrom-GitLoopyRuntimeArchitecture -Architecture Arm) `
    "the 32-bit ARM runtime name is translated to uname's"

# An architecture nobody has published is passed through lowered rather than
# dropped, so the refusal can still name the host it refused.
Assert-Equal "riscv64" (ConvertFrom-GitLoopyRuntimeArchitecture -Architecture RiscV64) `
    "an unpublished architecture still names itself"

# The control the three assertions above exist to serve: every architecture this
# runtime can report has to survive translation into an answer the shared fixture
# recognises. A deferred platform must be refused by its own recorded reason —
# losing that vocabulary in translation would silently downgrade "we chose not to
# ship this yet" into the same "nothing is published" sentence a typo produces.
$DeferredArm = @($Metadata["deferred_targets"]) |
    Where-Object { $_["arch"] -ceq "armv7" } | Select-Object -First 1
Assert-True ($null -ne $DeferredArm) "the fixture still defers 32-bit ARM Linux"

$ArmRefusal = Get-RefusalMessage {
    Select-GitLoopyTuiTarget -Metadata $ArtifactMetadata -System "Linux" `
        -Machine (ConvertFrom-GitLoopyRuntimeArchitecture -Architecture Arm) -Libc "gnu"
}
Assert-Contains $ArmRefusal ([string]$DeferredArm["reason"]) `
    "32-bit ARM Linux is deferred by name, not by absence"

# And the architectures that are published resolve to the artifact the fixture
# names for them, through the translation rather than around it.
foreach ($Pair in @(
        @("Linux", "X64", "gnu", "x86_64-unknown-linux-gnu"),
        @("Linux", "Arm64", "musl", "aarch64-unknown-linux-musl"),
        @("Darwin", "Arm64", "", "aarch64-apple-darwin"),
        @("Windows", "X64", "", "x86_64-pc-windows-msvc")
    )) {
    Assert-Equal $Pair[3] (
        Select-GitLoopyTuiTarget -Metadata $ArtifactMetadata -System $Pair[0] `
            -Machine (ConvertFrom-GitLoopyRuntimeArchitecture -Architecture $Pair[1]) `
            -Libc $Pair[2]
    ) "$($Pair[0]) $($Pair[1]) resolves through the translated architecture"
}

# --- Host libc --------------------------------------------------------------
#
# Linux's alone, and the one selection input the runtime cannot answer. Parsed
# from `ldd --version` text rather than probed, so the cases a real host produces
# are pinned rather than described.

Assert-Equal "musl" (ConvertFrom-GitLoopyLddReport -Report @"
musl libc (x86_64)
Version 1.2.4
"@) "musl identifies itself"
Assert-Equal "gnu" (
    ConvertFrom-GitLoopyLddReport -Report "ldd (Ubuntu GLIBC 2.35-0ubuntu3.6) 2.35"
) "glibc identifies itself"
Assert-Equal "" (ConvertFrom-GitLoopyLddReport -Report "command not found") `
    "an unrecognized C library is not guessed at"

# The host this suite runs on has to resolve to a published artifact, or nothing
# below can be exercised at all.
$HostShape = Get-GitLoopyTuiHostShape
Assert-True (-not [string]::IsNullOrEmpty($HostShape.System)) `
    "the runtime names the operating system it is on"
$HostTriple = Get-GitLoopyTuiHostTarget -Metadata $ArtifactMetadata
$HostNames = Get-GitLoopyTuiArtifactName -Metadata $ArtifactMetadata -Triple $HostTriple

# --- A published Release, served over a loopback socket ---------------------
#
# A real HTTP transfer with no network: `Invoke-WebRequest` against a
# `TcpListener` bound to 127.0.0.1, so the download branch and its failure are
# proven rather than described.

function Start-LoopbackReleaseServer {
    param(
        [Parameter(Mandatory)]
        [string]$Root
    )

    $Listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $Listener.Start()
    $Runspace = [Management.Automation.Runspaces.RunspaceFactory]::CreateRunspace()
    $Runspace.Open()
    $Runspace.SessionStateProxy.SetVariable("Listener", $Listener)
    $Runspace.SessionStateProxy.SetVariable("Root", $Root)
    $Shell = [PowerShell]::Create()
    $Shell.Runspace = $Runspace
    [void]$Shell.AddScript({
            while ($true) {
                try {
                    $Client = $Listener.AcceptTcpClient()
                }
                catch {
                    break
                }
                try {
                    $Stream = $Client.GetStream()
                    $Request = [Text.StringBuilder]::new()
                    $Byte = [byte[]]::new(1)
                    while ($true) {
                        if ($Stream.Read($Byte, 0, 1) -le 0) {
                            break
                        }
                        [void]$Request.Append([char]$Byte[0])
                        if ($Request.Length -ge 4 -and
                            $Request.ToString($Request.Length - 4, 4) -eq "`r`n`r`n") {
                            break
                        }
                    }
                    $Target = ($Request.ToString() -split "`r`n")[0] -split " "
                    $Name = if ($Target.Length -ge 2) {
                        [IO.Path]::GetFileName($Target[1])
                    }
                    else {
                        ""
                    }
                    $File = Join-Path $Root $Name
                    if (-not [string]::IsNullOrEmpty($Name) -and [IO.File]::Exists($File)) {
                        $Body = [IO.File]::ReadAllBytes($File)
                        $Status = "200 OK"
                    }
                    else {
                        $Body = [Text.Encoding]::ASCII.GetBytes("no such artifact")
                        $Status = "404 Not Found"
                    }
                    $Head = [Text.Encoding]::ASCII.GetBytes(
                        "HTTP/1.1 $Status`r`nContent-Length: $($Body.Length)`r`n" +
                        "Content-Type: application/octet-stream`r`nConnection: close`r`n`r`n"
                    )
                    $Stream.Write($Head, 0, $Head.Length)
                    $Stream.Write($Body, 0, $Body.Length)
                    $Stream.Flush()
                }
                catch {
                }
                finally {
                    $Client.Close()
                }
            }
        })
    return [pscustomobject]@{
        Listener = $Listener
        Shell = $Shell
        Handle = $Shell.BeginInvoke()
        Runspace = $Runspace
        BaseUrl = "http://127.0.0.1:$(([Net.IPEndPoint]$Listener.LocalEndpoint).Port)"
    }
}

function Stop-LoopbackReleaseServer {
    param(
        [Parameter(Mandatory)]
        [object]$Server
    )

    $Server.Listener.Stop()
    try {
        [void]$Server.Shell.EndInvoke($Server.Handle)
    }
    catch {
    }
    $Server.Shell.Dispose()
    $Server.Runspace.Dispose()
}

$ReleaseDir = New-Scratch -Name release

function Publish-FakeRelease {
    param(
        [Parameter(Mandatory)]
        [string]$Into,
        [Parameter(Mandatory)]
        [string]$ReportedVersion,
        [int]$Maximum = $SchemaVersion
    )

    if (Test-Path -LiteralPath $Into) {
        Remove-Item -LiteralPath $Into -Recurse -Force
    }
    $PayloadDir = Join-Path $Into "payload"
    [void](New-Item -ItemType Directory -Path $PayloadDir -Force)
    New-FakeHelperScript -Path (Join-Path $PayloadDir $HostNames.Executable) `
        -ReportedVersion $ReportedVersion -Maximum $Maximum
    $ArchivePath = Join-Path $Into $HostNames.Archive
    if ($HostNames.Archive.EndsWith(".zip", [StringComparison]::OrdinalIgnoreCase)) {
        Compress-Archive -Path (Join-Path $PayloadDir $HostNames.Executable) `
            -DestinationPath $ArchivePath -Force
    }
    else {
        & tar -cJf $ArchivePath -C $PayloadDir $HostNames.Executable
        if ($LASTEXITCODE -ne 0) {
            throw "FAIL: could not publish the fake Release archive"
        }
    }
    [IO.File]::WriteAllText(
        (Join-Path $Into $HostNames.Checksum),
        "$(Get-GitLoopyTuiDigest -Path $ArchivePath)  $($HostNames.Archive)`n"
    )
    return $ArchivePath
}

[void](Publish-FakeRelease -Into $ReleaseDir -ReportedVersion 4.5.6)
$Server = Start-LoopbackReleaseServer -Root $ReleaseDir
try {
    $Fetched = Join-Path (New-Scratch -Name fetch) $HostNames.Archive
    Save-GitLoopyTuiArtifact -Uri "$($Server.BaseUrl)/$($HostNames.Archive)" `
        -Destination $Fetched
    Assert-Equal (Get-GitLoopyTuiDigest -Path (Join-Path $ReleaseDir $HostNames.Archive)) (
        Get-GitLoopyTuiDigest -Path $Fetched
    ) "a real HTTP transfer delivers the published bytes"

    Assert-Contains (Get-RefusalMessage {
            Save-GitLoopyTuiArtifact -Uri "$($Server.BaseUrl)/absent.tar.xz" `
                -Destination (Join-Path (Split-Path -Parent $Fetched) "absent.tar.xz")
        }) "cannot download" "an HTTP error is a failed download, not an archive"
}
finally {
    Stop-LoopbackReleaseServer -Server $Server
}

# --- The installer, end to end ----------------------------------------------
#
# A fake clone with its own VERSION, installing from a fake published Release.

$CliDir = New-Scratch -Name install
$Pwsh = if ($null -ne (Get-Command pwsh -ErrorAction SilentlyContinue)) {
    (Get-Command pwsh).Source
}
else {
    Join-Path $PSHOME (if ($IsWindows) { "pwsh.exe" } else { "pwsh" })
}

function New-FakeClone {
    param(
        [Parameter(Mandatory)]
        [string]$Root,
        [Parameter(Mandatory)]
        [string]$Version
    )

    $Port = Join-Path $Root "git-loopy/powershell"
    [void](New-Item -ItemType Directory -Path $Port -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $Root "git-loopy/conformance") -Force)
    Copy-Item -Path (Join-Path $PortDir "*.ps1") -Destination $Port
    Copy-Item -Path (Join-Path $PortDir "*.psm1") -Destination $Port
    Copy-Item -LiteralPath $ArtifactMetadata `
        -Destination (Join-Path $Root "git-loopy/conformance")
    [IO.File]::WriteAllText((Join-Path $Root "VERSION"), "$Version`n")
    return (Join-Path $Port "install.ps1")
}

function Invoke-Installer {
    param(
        [Parameter(Mandatory)]
        [string]$Installer,
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [hashtable]$Environment = @{}
    )

    $Saved = @{}
    foreach ($Key in $Environment.Keys) {
        $Saved[$Key] = [Environment]::GetEnvironmentVariable($Key)
        [Environment]::SetEnvironmentVariable($Key, $Environment[$Key])
    }
    try {
        $Output = & $Pwsh -NoLogo -NoProfile -File $Installer @Arguments 2>&1 |
            Out-String
        return [pscustomobject]@{
            ExitCode = $LASTEXITCODE
            Output = $Output
        }
    }
    finally {
        foreach ($Key in $Saved.Keys) {
            [Environment]::SetEnvironmentVariable($Key, $Saved[$Key])
        }
    }
}

$LauncherName = if ($IsWindows) { "git-loopy.cmd" } else { "git-loopy" }

# 1. The opt-out keeps the Phase 1 launcher-only behaviour.
$OptOutClone = Join-Path $CliDir "opt-out"
$OptOutInstaller = New-FakeClone -Root $OptOutClone -Version 4.5.6
$OptOutBin = Join-Path $CliDir "opt-out-bin"
$OptOut = Invoke-Installer -Installer $OptOutInstaller `
    -Arguments @("-BinDir", $OptOutBin, "-NoTui")
Assert-Equal 0 $OptOut.ExitCode "-NoTui failed: $($OptOut.Output)"
Assert-True ([IO.File]::Exists((Join-Path $OptOutBin $LauncherName))) `
    "-NoTui skipped the launcher"
Assert-True (
    -not (Test-Path -LiteralPath (Join-Path $OptOutClone ".git-loopy/bin"))
) "-NoTui staged a helper anyway"
Assert-Contains $OptOut.Output "plain mode" "-NoTui says what a Run will look like"

# 2. PATH guidance is printed exactly when the shim is not discoverable.
Assert-Contains $OptOut.Output "is not on your PATH" `
    "an undiscoverable shim earns PATH guidance"
$OnPath = Invoke-Installer -Installer $OptOutInstaller `
    -Arguments @("-BinDir", $OptOutBin, "-NoTui") `
    -Environment @{ PATH = "$OptOutBin$([IO.Path]::PathSeparator)$env:PATH" }
Assert-Contains $OnPath.Output "Run it from inside any git repository" `
    "a discoverable shim is reported as ready to run"

# 3. A local artifact offered without its published manifest is refused, and the
#    refusal is about the missing manifest rather than about anything downloaded.
$AirgapClone = Join-Path $CliDir "airgap"
$AirgapInstaller = New-FakeClone -Root $AirgapClone -Version 4.5.6
$Lonely = Invoke-Installer -Installer $AirgapInstaller -Arguments @(
    "-BinDir", (Join-Path $CliDir "airgap-bin"),
    "-TuiArchive", (Join-Path $ReleaseDir $HostNames.Archive)
)
Assert-True ($Lonely.ExitCode -ne 0) "a local artifact installed with no checksum manifest"
Assert-Contains $Lonely.Output "-TuiChecksum" `
    "a local artifact with no manifest names the option that would supply one"

# 4. Checksum drift is refused and the destination is left exactly as it was.
#    A sentinel stands in for a previously installed helper, so the
#    non-destructive promise is checked byte for byte rather than by asking a
#    fabricated binary what it is.
$DriftClone = Join-Path $CliDir "drift"
$DriftInstaller = New-FakeClone -Root $DriftClone -Version 4.5.6
$DriftSlot = Join-Path $DriftClone ".git-loopy/bin"
[void](New-Item -ItemType Directory -Path $DriftSlot -Force)
$Sentinel = Join-Path $DriftSlot $HostNames.Executable
[IO.File]::WriteAllText($Sentinel, "a previously verified helper")

$TamperedRelease = Join-Path $CliDir "tampered"
$TamperedArchive = Publish-FakeRelease -Into $TamperedRelease -ReportedVersion 4.5.6
[IO.File]::AppendAllText($TamperedArchive, "tampered")
$Drift = Invoke-Installer -Installer $DriftInstaller -Arguments @(
    "-BinDir", (Join-Path $CliDir "drift-bin"),
    "-TuiArchive", $TamperedArchive,
    "-TuiChecksum", (Join-Path $TamperedRelease $HostNames.Checksum)
)
Assert-True ($Drift.ExitCode -ne 0) "a tampered artifact installed successfully"
Assert-Contains $Drift.Output "SHA-256 checksum" "a tampered artifact is refused by checksum"
Assert-Contains $Drift.Output "plain mode" `
    "a refused helper says the Orchestrator still runs in plain mode"
Assert-Equal "a previously verified helper" ([IO.File]::ReadAllText($Sentinel)) `
    "a failed installation leaves the previously verified helper untouched"
Assert-Equal 1 (@(Get-ChildItem -LiteralPath $DriftSlot -Force).Count) `
    "a failed installation left staging debris beside the helper"

# 5. An unreachable Release is refused by download, and changes nothing either.
$Missing = Invoke-Installer -Installer $DriftInstaller -Arguments @(
    "-BinDir", (Join-Path $CliDir "drift-bin"),
    "-TuiBaseUrl", "file://$(Join-Path $CliDir "nowhere")"
)
Assert-True ($Missing.ExitCode -ne 0) "an unreachable Release installed something"
Assert-Contains $Missing.Output "cannot download" `
    "an unreachable Release is refused by download, not by checksum"
Assert-Equal "a previously verified helper" ([IO.File]::ReadAllText($Sentinel)) `
    "an unreachable Release leaves the installed helper untouched"

if ($CanRunFabricatedHelper) {
    # 6. The default installation stages both halves of the distribution, over a
    #    real HTTP transfer from the fake published Release.
    $Clone = Join-Path $CliDir "clone"
    $Installer = New-FakeClone -Root $Clone -Version 4.5.6
    $BinDir = Join-Path $CliDir "bin"
    $Server = Start-LoopbackReleaseServer -Root $ReleaseDir
    try {
        $Installed = Invoke-Installer -Installer $Installer -Arguments @(
            "-BinDir", $BinDir, "-TuiBaseUrl", $Server.BaseUrl
        )
    }
    finally {
        Stop-LoopbackReleaseServer -Server $Server
    }
    Assert-Equal 0 $Installed.ExitCode "the default installation failed: $($Installed.Output)"
    $Helper = Join-Path (Join-Path $Clone ".git-loopy/bin") $HostNames.Executable
    Assert-True ([IO.File]::Exists((Join-Path $BinDir $LauncherName))) `
        "the launcher shim was not installed"
    Assert-Equal "git-loopy-tui 4.5.6" ((& $Helper --version | Out-String).Trim()) `
        "the staged helper is the Release this clone pins"
    Assert-Contains $Installed.Output $Helper "the installation reports where the helper landed"

    # 7. An air-gapped host installs from local files and never reaches for a URL.
    $Airgap = Invoke-Installer -Installer $AirgapInstaller -Arguments @(
        "-BinDir", (Join-Path $CliDir "airgap-bin"),
        "-TuiArchive", (Join-Path $ReleaseDir $HostNames.Archive),
        "-TuiChecksum", (Join-Path $ReleaseDir $HostNames.Checksum),
        "-TuiBaseUrl", "file:///nonexistent"
    )
    Assert-Equal 0 $Airgap.ExitCode "the air-gapped installation failed: $($Airgap.Output)"
    $AirgapHelper = Join-Path (Join-Path $AirgapClone ".git-loopy/bin") $HostNames.Executable
    Assert-Equal "git-loopy-tui 4.5.6" ((& $AirgapHelper --version | Out-String).Trim()) `
        "a local artifact installs when its published checksum matches"

    # 8. A helper from another Release is refused before it is activated.
    $Foreign = Join-Path $CliDir "foreign"
    [void](Publish-FakeRelease -Into $Foreign -ReportedVersion 9.9.9)
    $ForeignResult = Invoke-Installer -Installer $Installer -Arguments @(
        "-BinDir", $BinDir, "-TuiBaseUrl", "file://$Foreign"
    )
    Assert-True ($ForeignResult.ExitCode -ne 0) "a helper from another Release installed"
    Assert-Contains $ForeignResult.Output "9.9.9" "the refused Release version is named"
    Assert-Equal "git-loopy-tui 4.5.6" ((& $Helper --version | Out-String).Trim()) `
        "a refused Release leaves the installed helper untouched"

    # 9. A helper that cannot decode this Event schema is refused too.
    $Incapable = Join-Path $CliDir "incapable"
    [void](Publish-FakeRelease -Into $Incapable -ReportedVersion 4.5.6 -Maximum 0)
    $IncapableResult = Invoke-Installer -Installer $Installer -Arguments @(
        "-BinDir", $BinDir, "-TuiBaseUrl", "file://$Incapable"
    )
    Assert-True ($IncapableResult.ExitCode -ne 0) "an incapable helper installed"
    Assert-Contains $IncapableResult.Output "Event schema" `
        "an incapable helper is refused by capability"
    Assert-Equal "git-loopy-tui 4.5.6" ((& $Helper --version | Out-String).Trim()) `
        "an incapable helper leaves the installed one untouched"
}
else {
    [Console]::Out.WriteLine(
        "  (skipped: the cases that must execute a staged helper need a real " +
        "executable, which this platform cannot fabricate)")
}

# --- A Run never installs software ------------------------------------------
#
# The other half of the promise. Installation is an explicit act by the operator
# and happens exactly once, in install.ps1; the Orchestrator only ever *discovers*
# a helper somebody already staged. Nothing on the Run path may reach for a
# network or for this module, so that is asserted rather than merely documented.

foreach ($RunPathFile in @(
        "GitLoopy.Continuation.psm1", "GitLoopy.Events.psm1",
        "GitLoopy.Orchestrator.psm1", "GitLoopy.Tui.psm1", "git-loopy.ps1"
    )) {
    $Text = Get-Content -LiteralPath (Join-Path $PortDir $RunPathFile) -Raw
    foreach ($Forbidden in @(
            "Invoke-WebRequest", "Invoke-RestMethod", "curl", "wget",
            "GitLoopy.TuiInstall"
        )) {
        Assert-True (-not $Text.Contains($Forbidden)) (
            "$RunPathFile reaches for a download on the Run path ($Forbidden)")
    }
}

foreach ($Path in $Workspaces) {
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
}

[Console]::Out.WriteLine("PowerShell TUI installer: ok")
