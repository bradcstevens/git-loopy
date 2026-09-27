Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PortDir = Split-Path -Parent $PSScriptRoot
$FixturePath = Join-Path (Split-Path -Parent $PortDir) "conformance/release-line.json"
$ModulePath = Join-Path $PortDir "GitLoopy.Release.psm1"

Import-Module $ModulePath -Force

function Assert-Equal {
    param(
        [AllowNull()]
        [object]$Expected,
        [AllowNull()]
        [object]$Actual,
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
        [string]$Needle,
        [string]$Haystack,
        [string]$Description
    )

    if (-not $Haystack.Contains($Needle, [StringComparison]::Ordinal)) {
        throw "FAIL: $Description`nmissing:  $Needle`ncontent:  $Haystack"
    }
}

$Fixture = Get-Content -LiteralPath $FixturePath -Raw | ConvertFrom-Json -AsHashtable

function Get-PowerShellCases {
    param([string]$Name)

    return @(
        foreach ($Case in @($Fixture[$Name])) {
            if (@($Case["distributions"]) -contains "powershell") {
                $Case
            }
        }
    )
}

$VersionPaths = @(
    "VERSION",
    "git-loopy/python/git_loopy/__init__.py",
    "git-loopy/python/git_loopy/VERSION",
    "git-loopy/python/pyproject.toml",
    "git-loopy/python/uv.lock",
    "git-loopy/tui/Cargo.toml",
    "git-loopy/tui/Cargo.lock",
    "git-loopy/tui/README.md",
    "git-loopy/conformance/release-version.json"
)

function Set-Utf8Text {
    param(
        [string]$Path,
        [string]$Content
    )

    [IO.Directory]::CreateDirectory((Split-Path -Parent $Path)) | Out-Null
    [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
}

function Get-Utf8Text {
    param([string]$Path)

    return [Text.UTF8Encoding]::new($false, $true).GetString(
        [IO.File]::ReadAllBytes($Path)
    )
}

function Get-HeadCommitPaths {
    param([string]$RepositoryRoot)

    return @(
        (& git -C $RepositoryRoot show --pretty=format: --name-only HEAD) |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
}

function Get-ReleaseFixtureContentWithVersions {
    param(
        [string]$Content,
        [string]$ReleaseVersion,
        [string]$PythonDistributionVersion
    )

    $Updated = [regex]::Replace(
        $Content,
        '(?m)^(\s*"expected_release_version"\s*:\s*)"[^"]*"(?=,\r?$)',
        ('${1}"' + $ReleaseVersion + '"'),
        1
    )
    return [regex]::Replace(
        $Updated,
        '(?m)^(\s*"expected_python_distribution_version"\s*:\s*)"[^"]*"(?=,\r?$)',
        ('${1}"' + $PythonDistributionVersion + '"'),
        1
    )
}

function New-ReleaseLineScratchRepository {
    param(
        [string]$ScratchRoot,
        [string]$Version,
        [AllowNull()]
        [string]$ReachableStableTag,
        [ValidateSet("LF", "CRLF")]
        [string]$LineEnding = "LF"
    )

    $Scratch = Join-Path $ScratchRoot ([guid]::NewGuid().Guid)
    [IO.Directory]::CreateDirectory($Scratch) | Out-Null
    foreach ($Path in $VersionPaths) {
        $Destination = Join-Path $Scratch $Path
        [IO.Directory]::CreateDirectory((Split-Path -Parent $Destination)) | Out-Null
        [IO.File]::Copy((Join-Path $RepositoryRoot $Path), $Destination)
        if ($LineEnding -ceq "CRLF") {
            Set-Utf8Text `
                -Path $Destination `
                -Content ((Get-Utf8Text -Path $Destination) -replace '\r?\n', "`r`n")
        }
    }
    # The live Release line may be any value (even a retired -dev.N one), so the
    # scratch copies are first rewritten textually to a known stable value.
    $Live = (Get-Utf8Text -Path (Join-Path $RepositoryRoot "VERSION")).Trim()
    $LiveMatch = [regex]::Match($Live, '\A([0-9]+\.[0-9]+\.[0-9]+)-(dev|alpha|beta|rc)\.([0-9]+)\z')
    if ($LiveMatch.Success) {
        $Spelling = @{ dev = "."; alpha = ""; beta = ""; rc = "" }[$LiveMatch.Groups[2].Value]
        $Short = @{ dev = "dev"; alpha = "a"; beta = "b"; rc = "rc" }[$LiveMatch.Groups[2].Value]
        $LivePython = "$($LiveMatch.Groups[1].Value)$Spelling$Short$($LiveMatch.Groups[3].Value)"
        foreach ($Path in $VersionPaths) {
            $Destination = Join-Path $Scratch $Path
            Set-Utf8Text `
                -Path $Destination `
                -Content (Get-Utf8Text -Path $Destination).Replace($Live, "0.0.1").Replace($LivePython, "0.0.1")
        }
    }
    Set-GitLoopyRepositoryReleaseVersion -RepositoryRoot $Scratch -Version $Version
    & git -C $Scratch init -q
    & git -C $Scratch config user.email tester@example.invalid
    & git -C $Scratch config user.name "Test Runner"
    & git -C $Scratch add -A
    & git -C $Scratch commit -qm "initial Release metadata"
    if (-not [string]::IsNullOrEmpty($ReachableStableTag)) {
        & git -C $Scratch tag "v$ReachableStableTag"
    }
    return $Scratch
}

function Get-Counter {
    param([object]$Value)

    return [bigint][long]$Value
}

foreach ($Case in Get-PowerShellCases "cases") {
    Assert-Equal $Case["bump_class"] (
        Resolve-GitLoopyBumpClass `
            -Labels ([string[]]@($Case["labels"])) `
            -LastStableVersion $Case["last_stable_version"]
    ) "Release-line Bump-class decision: $($Case["id"])"
}

foreach ($Case in Get-PowerShellCases "refusal_cases") {
    $Refusal = $null
    try {
        Resolve-GitLoopyBumpClass `
            -Labels ([string[]]@($Case["labels"])) `
            -LastStableVersion $Case["last_stable_version"] | Out-Null
    }
    catch {
        $Refusal = $_.Exception
    }
    if ($null -eq $Refusal) {
        throw "FAIL: Release-line Bump-class refusal was accepted: $($Case["id"])"
    }
    Assert-Equal "Release-line Bump class: $($Case["reason"])" $Refusal.Message (
        "Release-line Bump-class refusal reason: $($Case["id"])"
    )
    if ($Case.ContainsKey("refused_label")) {
        Assert-Equal $Case["refused_label"] $Refusal.Data["refused_label"] (
            "Release-line refused label: $($Case["id"])"
        )
    }
    if ($Case.ContainsKey("conflicting_labels")) {
        Assert-Equal (@($Case["conflicting_labels"]) -join "|") (
            @($Refusal.Data["conflicting_labels"]) -join "|"
        ) "Release-line conflicting labels: $($Case["id"])"
    }
}

foreach ($Group in @("ratchet_cases", "counter_cases")) {
    foreach ($Case in Get-PowerShellCases $Group) {
        $Line = Invoke-GitLoopyReleaseLineAdvance `
            -LastStableVersion $Case["last_stable_version"] `
            -CurrentTarget $Case["current_target"] `
            -CurrentStage $Case["current_stage"] `
            -CurrentCounter (Get-Counter $Case["current_counter"]) `
            -BumpClass $Case["bump_class"]
        Assert-Equal $Case["new_target"] $Line.Target "Release-line target: $($Case["id"])"
        Assert-Equal $Case["new_stage"] $Line.Stage "Release-line stage: $($Case["id"])"
        Assert-Equal (Get-Counter $Case["new_counter"]) $Line.Counter "Release-line counter: $($Case["id"])"
        Assert-Equal $Case["resulting_version"] $Line.Version "Release-line version: $($Case["id"])"
    }
}

foreach ($Case in Get-PowerShellCases "stage_cases") {
    Assert-Equal $Case["resulting_version"] (
        Step-GitLoopyReleaseStage -CurrentVersion $Case["current_version"] -Stage $Case["stage"]
    ) "Release-line stage advance: $($Case["id"])"
}

foreach ($Case in Get-PowerShellCases "stage_refusal_cases") {
    $Refusal = $null
    try {
        Step-GitLoopyReleaseStage -CurrentVersion $Case["current_version"] -Stage $Case["stage"] | Out-Null
    }
    catch {
        $Refusal = $_.Exception
    }
    if ($null -eq $Refusal) {
        throw "FAIL: Release-line stage refusal was accepted: $($Case["id"])"
    }
    Assert-Equal "Release-line stage: $($Case["reason"])" $Refusal.Message (
        "Release-line stage refusal reason: $($Case["id"])"
    )
}

foreach ($Case in Get-PowerShellCases "promotion_cases") {
    Assert-Equal $Case["resulting_version"] (
        Get-GitLoopyClosedMilestonePromotion `
            -CurrentVersion $Case["current_version"] `
            -MilestoneTitle $Case["milestone_title"] `
            -MilestoneState $Case["milestone_state"]
    ) "closed milestone Promotion: $($Case["id"])"
}

foreach ($Case in Get-PowerShellCases "order_independence_cases") {
    foreach ($Order in @($Case["integration_orders"])) {
        $Target = [string]$Case["current_target"]
        $Stage = $Case["current_stage"]
        $Counter = Get-Counter $Case["current_counter"]
        foreach ($BumpClass in @($Order)) {
            $Line = Invoke-GitLoopyReleaseLineAdvance `
                -LastStableVersion $Case["last_stable_version"] `
                -CurrentTarget $Target `
                -CurrentStage $Stage `
                -CurrentCounter $Counter `
                -BumpClass $BumpClass
            $Target = $Line.Target
            $Stage = $Line.Stage
            $Counter = $Line.Counter
        }
        Assert-Equal $Case["resulting_target"] $Line.Target (
            "Release-line Integration-order target: $($Case["id"])"
        )
        Assert-Equal $Case["resulting_stage"] $Line.Stage (
            "Release-line Integration-order stage: $($Case["id"])"
        )
        Assert-Equal (Get-Counter $Case["resulting_counter"]) $Line.Counter (
            "Release-line Integration-order counter: $($Case["id"])"
        )
        Assert-Equal $Case["resulting_version"] $Line.Version (
            "Release-line Integration-order version: $($Case["id"])"
        )
    }
}

$RepositoryRoot = (Resolve-Path (Join-Path $PortDir "../..")).Path
$ScratchRoot = Join-Path $RepositoryRoot ".git-loopy-powershell-release-tests"
try {
    [IO.Directory]::CreateDirectory($ScratchRoot) | Out-Null

    $Scratch = New-ReleaseLineScratchRepository `
        -ScratchRoot $ScratchRoot `
        -Version "1.2.3" `
        -ReachableStableTag $null
    Import-Module $ModulePath -Force

    $Advance = Invoke-GitLoopyRepositoryReleaseLineAdvance `
        -RepositoryRoot $Scratch `
        -Labels @("v1.2.4")
    Assert-Equal "patch" $Advance.BumpClass "a closed patch resolves its Bump class"
    Assert-Equal "1.2.4" $Advance.Target "a closed patch ratchets its target"
    Assert-Equal "1.2.4-alpha.1" $Advance.Version (
        "a closed patch advances every Release metadata copy"
    )
    $FirstFragmentPath = Join-Path $Scratch "docs/releases/v1.2.4-alpha.1.md"
    Assert-Equal (
        "# git-loopy 1.2.4-alpha.1`n`n" +
        "This development fragment advances the Release line to ``1.2.4-alpha.1`` on the way to stable ``1.2.4``.`n"
    ) (Get-Utf8Text -Path $FirstFragmentPath) (
        "a Release-line advance writes its alpha fragment"
    )
    Assert-Equal $true (
        (Get-HeadCommitPaths -RepositoryRoot $Scratch) -contains "docs/releases/v1.2.4-alpha.1.md"
    ) "the advance commit includes the fragment note path"
    Assert-Equal "1.2.4-alpha.1" (
        Get-GitLoopyReleaseVersion -Path (Join-Path $Scratch "VERSION")
    ) "the Release authority advanced"
    Assert-Equal "1.2.4-alpha.1" (
        [regex]::Match(
            (Get-Content -LiteralPath (Join-Path $Scratch "git-loopy/tui/Cargo.toml") -Raw),
            '(?m)^version = "([^"]+)"'
        ).Groups[1].Value
    ) "the Rust manifest copy advanced"
    Assert-Equal "1.2.4a1" (
        [regex]::Match(
            (Get-Content -LiteralPath (Join-Path $Scratch "git-loopy/python/uv.lock") -Raw),
            '(?s)name = "git-loopy".*?version = "([^"]+)"'
        ).Groups[1].Value
    ) "the Python lockfile copy normalizes alpha.N"
    $ReleaseFixturePath = Join-Path $Scratch "git-loopy/conformance/release-version.json"
    $ReleaseFixture = Get-Utf8Text -Path $ReleaseFixturePath | ConvertFrom-Json -AsHashtable
    Assert-Equal "1.2.4-alpha.1" $ReleaseFixture["expected_release_version"] (
        "a Release-line advance updates the fixture's public SemVer expectation"
    )
    Assert-Equal "1.2.4a1" $ReleaseFixture["expected_python_distribution_version"] (
        "a Release-line advance updates the fixture's normalized PEP 440 expectation"
    )
    Assert-Equal (
        Get-ReleaseFixtureContentWithVersions `
            -Content (Get-Utf8Text -Path (Join-Path $RepositoryRoot "git-loopy/conformance/release-version.json")) `
            -ReleaseVersion "1.2.4-alpha.1" `
            -PythonDistributionVersion "1.2.4a1"
    ) (Get-Utf8Text -Path $ReleaseFixturePath) (
        "a Release-line advance preserves every other release-version fixture field"
    )

    $DuplicateFixtureScratch = New-ReleaseLineScratchRepository `
        -ScratchRoot $ScratchRoot `
        -Version "1.2.3" `
        -ReachableStableTag $null
    $DuplicateFixturePath = Join-Path $DuplicateFixtureScratch "git-loopy/conformance/release-version.json"
    $DuplicateFixtureContent = Get-Utf8Text -Path $DuplicateFixturePath
    Set-Utf8Text `
        -Path $DuplicateFixturePath `
        -Content $DuplicateFixtureContent.Replace(
            '  "expected_release_version": "1.2.3",',
            "  `"expected_release_version`": `"1.2.3`",`n" +
                '  "expected_release_version": "1.2.3",'
        )
    $MetadataBeforeDuplicateFixtureFailure = @{}
    foreach ($Path in $VersionPaths) {
        $MetadataBeforeDuplicateFixtureFailure[$Path] = Get-Utf8Text -Path (
            Join-Path $DuplicateFixtureScratch $Path
        )
    }
    try {
        Set-GitLoopyRepositoryReleaseVersion `
            -RepositoryRoot $DuplicateFixtureScratch `
            -Version "1.2.4-alpha.1"
        throw "FAIL: duplicate release-version fixture fields were accepted"
    }
    catch {
        Assert-Contains "expected_release_version" $_.Exception.Message (
            "a duplicate release-version fixture field is rejected explicitly"
        )
    }
    foreach ($Path in $VersionPaths) {
        Assert-Equal $MetadataBeforeDuplicateFixtureFailure[$Path] (
            Get-Utf8Text -Path (Join-Path $DuplicateFixtureScratch $Path)
        ) "a rejected release-version fixture leaves every Release metadata copy unchanged: $Path"
    }

    $CrLfScratch = New-ReleaseLineScratchRepository `
        -ScratchRoot $ScratchRoot `
        -Version "1.2.3" `
        -ReachableStableTag "1.2.3" `
        -LineEnding "CRLF"
    $CrLfProject = Get-Utf8Text -Path (
        Join-Path $CrLfScratch "git-loopy/python/pyproject.toml"
    )
    Assert-Equal "1.2.3" (
        [regex]::Match($CrLfProject, '(?m)^version = "([^"]+)"').Groups[1].Value
    ) "a CRLF Python project manifest advances its Release version"
    Assert-Equal $false (
        [regex]::IsMatch($CrLfProject, '(?<!\r)\n')
    ) "a CRLF Python project manifest preserves its line endings"
    $CrLfReleaseFixture = Get-Utf8Text -Path (
        Join-Path $CrLfScratch "git-loopy/conformance/release-version.json"
    )
    Assert-Equal $false (
        [regex]::IsMatch($CrLfReleaseFixture, '(?<!\r)\n')
    ) "a CRLF release-version fixture preserves its line endings"

    $InvalidFixtureScratch = New-ReleaseLineScratchRepository `
        -ScratchRoot $ScratchRoot `
        -Version "1.2.3" `
        -ReachableStableTag $null
    $InvalidFixturePath = Join-Path $InvalidFixtureScratch "git-loopy/conformance/release-version.json"
    Set-Utf8Text `
        -Path $InvalidFixturePath `
        -Content (Get-Utf8Text -Path $InvalidFixturePath).Replace(
            '"expected_python_distribution_version": "1.2.3"',
            '"expected_python_distribution_version": false'
        )
    try {
        Set-GitLoopyRepositoryReleaseVersion `
            -RepositoryRoot $InvalidFixtureScratch `
            -Version "1.2.4-alpha.1"
        throw "FAIL: an invalid release-version fixture field type was accepted"
    }
    catch {
        Assert-Contains "must be a JSON string" $_.Exception.Message (
            "an invalid release-version fixture field type is rejected explicitly"
        )
    }

    Set-Utf8Text -Path $InvalidFixturePath -Content ($DuplicateFixtureContent + "`ninvalid")
    $MalformedFixtureRejected = $false
    try {
        Set-GitLoopyRepositoryReleaseVersion `
            -RepositoryRoot $InvalidFixtureScratch `
            -Version "1.2.4-alpha.1"
    }
    catch {
        $MalformedFixtureRejected = $true
    }
    Assert-Equal $true $MalformedFixtureRejected (
        "malformed JSON is refused even when both live fields look valid"
    )
    Assert-Equal "1.2.3" (
        (Get-Utf8Text -Path (Join-Path $InvalidFixtureScratch "VERSION")).Trim()
    ) "malformed fixture refusal preserves the repository Release version"

    $SecondAdvance = Invoke-GitLoopyRepositoryReleaseLineAdvance `
        -RepositoryRoot $Scratch -Labels @("ready-for-agent", "v1.3.0")
    Assert-Equal "1.3.0-alpha.2" $SecondAdvance.Version (
        "a second closure preserves the running Release-line counter"
    )
    Assert-Equal "chore(release): advance Release line to 1.3.0-alpha.2" (
        (& git -C $Scratch log -1 --format=%s)
    ) "the Release line is committed after every metadata copy changes"
    $Major = Invoke-GitLoopyRepositoryReleaseLineAdvance `
        -RepositoryRoot $Scratch -Labels @("v2.0.0")
    Assert-Equal "2.0.0-alpha.3" $Major.Version (
        "a major target raises the line without cutting stable"
    )
    Assert-Equal "major" $Major.BumpClass "a major target label resolves against the last stable Release"
    Assert-Equal $false (
        [IO.File]::Exists((Join-Path $Scratch "docs/releases/v2.0.0.md"))
    ) "a major advance writes no stable draft"
    $ReleaseCommitCount = [int](& git -C $Scratch rev-list --count HEAD)
    Assert-Equal $null (
        Invoke-GitLoopyRepositoryReleaseLineAdvance `
            -RepositoryRoot $Scratch -Labels @("ready-for-agent", "vendor", "V9.0.0")
    ) "an issue without a Release-target label does not advance the Release line"
    Assert-Equal $ReleaseCommitCount ([int](& git -C $Scratch rev-list --count HEAD)) (
        "an unlabelled issue does not create a Release commit"
    )
    try {
        Invoke-GitLoopyRepositoryReleaseLineAdvance -RepositoryRoot $Scratch -Labels @("v1.2.9") | Out-Null
        throw "FAIL: an unreachable Release target was accepted"
    }
    catch {
        Assert-Contains "unreachable_release_target" $_.Exception.Message (
            "an unreachable Release target is refused at the repository seam"
        )
    }

    $TaggedScratch = New-ReleaseLineScratchRepository `
        -ScratchRoot $ScratchRoot `
        -Version "1.3.0-beta.2" `
        -ReachableStableTag "1.2.3"
    Import-Module $ModulePath -Force
    $BetaAdvance = Invoke-GitLoopyRepositoryReleaseLineAdvance `
        -RepositoryRoot $TaggedScratch -Labels @("v1.2.4")
    Assert-Equal "1.3.0-beta.3" $BetaAdvance.Version (
        "a bump below the target keeps the beta stage and resolves against the reachable stable tag"
    )
    Assert-Equal "1.3.0b3" (
        [regex]::Match(
            (Get-Content -LiteralPath (Join-Path $TaggedScratch "git-loopy/python/uv.lock") -Raw),
            '(?s)name = "git-loopy".*?version = "([^"]+)"'
        ).Groups[1].Value
    ) "the Python distribution spells beta as bN"

    $Fragments = @(
        "v2.0.0-rc.1.md", "v2.0.0-alpha.10.md", "v2.0.0-beta.1.md", "v2.0.0-alpha.2.md", "v2.0.0-dev.3.md"
    )
    foreach ($Name in $Fragments) {
        $Version = $Name.Substring(1, $Name.Length - 4)
        Set-Utf8Text `
            -Path (Join-Path $Scratch "docs/releases/$Name") `
            -Content "# git-loopy $Version`n`nBody of $Version.`n"
    }
    $StableNotes = & (Get-Module GitLoopy.Release) {
        param($Root)
        New-GitLoopyStableReleaseNotesContent `
            -Version "2.0.0" `
            -Fragments (Get-GitLoopyReleaseTargetFragments `
                -RepositoryRoot $Root `
                -StableVersion "2.0.0" `
                -PendingFragments @([pscustomobject]@{
                    RelativePath = "docs/releases/v2.0.0-alpha.3.md"
                    Version = "2.0.0-alpha.3"
                    Content = "# git-loopy 2.0.0-alpha.3`n`nPending body.`n"
                }))
    } $Scratch
    $Headings = @([regex]::Matches($StableNotes, '(?m)^### (.+)$') | ForEach-Object { $_.Groups[1].Value })
    Assert-Equal "2.0.0-alpha.2|2.0.0-alpha.3|2.0.0-alpha.10|2.0.0-beta.1|2.0.0-rc.1" ($Headings -join "|") (
        "a stable draft composes every stage's fragments alpha < beta < rc, then by counter, ignoring dev.N"
    )
    Assert-Contains "git-loopy 2.0.0 was promoted from the committed development fragments below." $StableNotes (
        "the stable draft keeps its development-fragment wording"
    )
}
finally {
    if ([IO.Directory]::Exists($ScratchRoot)) {
        Remove-Item -LiteralPath $ScratchRoot -Recurse -Force -ErrorAction Stop
    }
}

Write-Output "PowerShell Release-line conformance: ok"
