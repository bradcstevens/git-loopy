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

foreach ($Case in Get-PowerShellCases "cases") {
    Assert-Equal $Case["bump_class"] (
        Resolve-GitLoopyBumpClass -Labels @($Case["labels"])
    ) "Release-line Bump-class decision: $($Case["id"])"
}

foreach ($Case in Get-PowerShellCases "refusal_cases") {
    try {
        Resolve-GitLoopyBumpClass -Labels @($Case["labels"]) | Out-Null
        throw "FAIL: Release-line Bump-class refusal was accepted: $($Case["id"])"
    }
    catch {
        if ($_.Exception.Message -notmatch [regex]::Escape([string]$Case["reason"])) {
            throw
        }
    }
}

foreach ($Group in @("ratchet_cases", "counter_cases")) {
    foreach ($Case in Get-PowerShellCases $Group) {
        $Line = Invoke-GitLoopyReleaseLineAdvance `
            -LastStableVersion $Case["last_stable_version"] `
            -CurrentTarget $Case["current_target"] `
            -CurrentCounter $Case["current_counter"] `
            -BumpClass $Case["bump_class"]
        Assert-Equal $Case["new_target"] $Line.Target "Release-line target: $($Case["id"])"
        Assert-Equal $Case["new_counter"] $Line.Counter "Release-line counter: $($Case["id"])"
        Assert-Equal $Case["resulting_version"] $Line.Version "Release-line version: $($Case["id"])"
    }
}

foreach ($Case in Get-PowerShellCases "promotion_cases") {
    Assert-Equal $Case["resulting_version"] (
        Get-GitLoopyClosedMilestonePromotion `
            -CurrentVersion $Case["current_version"] `
            -MilestoneTitle $Case["milestone_title"] `
            -MilestoneState $Case["milestone_state"]
    ) "closed milestone Promotion: $($Case["id"])"
}

foreach ($Case in Get-PowerShellCases "bump_promotion_cases") {
    $Advanced = Invoke-GitLoopyReleaseLineAdvance `
        -LastStableVersion $Case["last_stable_version"] `
        -CurrentTarget $Case["current_target"] `
        -CurrentCounter $Case["current_counter"] `
        -BumpClass $Case["bump_class"]
    Assert-Equal $Case["resulting_version"] (
        (Invoke-GitLoopyReleaseLinePromotion `
            -ReleaseLine $Advanced -BumpClass $Case["bump_class"]).Version
    ) "Bump-class Promotion exemption: $($Case["id"])"
}

foreach ($Case in Get-PowerShellCases "order_independence_cases") {
    foreach ($Order in @($Case["integration_orders"])) {
        $Target = [string]$Case["current_target"]
        [int]$Counter = $Case["current_counter"]
        foreach ($BumpClass in @($Order)) {
            $Line = Invoke-GitLoopyReleaseLineAdvance `
                -LastStableVersion $Case["last_stable_version"] `
                -CurrentTarget $Target `
                -CurrentCounter $Counter `
                -BumpClass $BumpClass
            $Target = $Line.Target
            $Counter = $Line.Counter
        }
        Assert-Equal $Case["resulting_target"] $Line.Target (
            "Release-line Integration-order target: $($Case["id"])"
        )
        Assert-Equal $Case["resulting_counter"] $Line.Counter (
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
        -Labels @("semver:patch")
    Assert-Equal "patch" $Advance.BumpClass "a closed patch resolves its Bump class"
    Assert-Equal "1.2.4" $Advance.Target "a closed patch ratchets its target"
    Assert-Equal "1.2.4-dev.1" $Advance.Version (
        "a closed patch advances every Release metadata copy"
    )
    $FirstFragmentPath = Join-Path $Scratch "docs/releases/v1.2.4-dev.1.md"
    Assert-Equal (
        "# git-loopy 1.2.4-dev.1`n`n" +
        "This development fragment advances the Release line to ``1.2.4-dev.1`` on the way to stable ``1.2.4``.`n"
    ) (Get-Utf8Text -Path $FirstFragmentPath) (
        "a Release-line advance writes its dev.N fragment"
    )
    Assert-Equal $true (
        (Get-HeadCommitPaths -RepositoryRoot $Scratch) -contains "docs/releases/v1.2.4-dev.1.md"
    ) "the advance commit includes the fragment note path"
    Assert-Equal "1.2.4-dev.1" (
        Get-GitLoopyReleaseVersion -Path (Join-Path $Scratch "VERSION")
    ) "the Release authority advanced"
    Assert-Equal "1.2.4-dev.1" (
        [regex]::Match(
            (Get-Content -LiteralPath (Join-Path $Scratch "git-loopy/tui/Cargo.toml") -Raw),
            '(?m)^version = "([^"]+)"'
        ).Groups[1].Value
    ) "the Rust manifest copy advanced"
    Assert-Equal "1.2.4.dev1" (
        [regex]::Match(
            (Get-Content -LiteralPath (Join-Path $Scratch "git-loopy/python/uv.lock") -Raw),
            '(?s)name = "git-loopy".*?version = "([^"]+)"'
        ).Groups[1].Value
    ) "the Python lockfile copy normalizes dev.N"
    $ReleaseFixturePath = Join-Path $Scratch "git-loopy/conformance/release-version.json"
    $ReleaseFixture = Get-Utf8Text -Path $ReleaseFixturePath | ConvertFrom-Json -AsHashtable
    Assert-Equal "1.2.4-dev.1" $ReleaseFixture["expected_release_version"] (
        "a Release-line advance updates the fixture's public SemVer expectation"
    )
    Assert-Equal "1.2.4.dev1" $ReleaseFixture["expected_python_distribution_version"] (
        "a Release-line advance updates the fixture's normalized PEP 440 expectation"
    )
    Assert-Equal (
        Get-ReleaseFixtureContentWithVersions `
            -Content (Get-Utf8Text -Path (Join-Path $RepositoryRoot "git-loopy/conformance/release-version.json")) `
            -ReleaseVersion "1.2.4-dev.1" `
            -PythonDistributionVersion "1.2.4.dev1"
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
            -Version "1.2.4-dev.1"
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
            -Version "1.2.4-dev.1"
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
            -Version "1.2.4-dev.1"
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
        -RepositoryRoot $Scratch -Labels @("semver:minor")
    Assert-Equal "1.3.0-dev.2" $SecondAdvance.Version (
        "a second closure preserves the running Release-line counter"
    )
    Set-Utf8Text `
        -Path (Join-Path $Scratch "docs/releases/v2.0.0-dev.1.md") `
        -Content "# git-loopy 2.0.0-dev.1`n`nFirst accumulated fragment.`n"
    Set-Utf8Text `
        -Path (Join-Path $Scratch "docs/releases/v2.0.0-dev.2.md") `
        -Content "# git-loopy 2.0.0-dev.2`n`nSecond accumulated fragment.`n"
    & git -C $Scratch add -- docs/releases/v2.0.0-dev.1.md docs/releases/v2.0.0-dev.2.md
    & git -C $Scratch commit -qm "seed accumulated Release fragments"
    Assert-Equal "chore(release): advance Release line to 1.3.0-dev.2" (
        (& git -C $Scratch log -2 --format=%s | Select-Object -Last 1)
    ) "the Release line is committed after every metadata copy changes"
    $Major = Invoke-GitLoopyRepositoryReleaseLineAdvance `
        -RepositoryRoot $Scratch -Labels @("semver:major")
    Assert-Equal "2.0.0" $Major.Version (
        "a major Bump class cuts stable without a milestone"
    )
    $StableNotes = Get-Utf8Text -Path (Join-Path $Scratch "docs/releases/v2.0.0.md")
    Assert-Contains "### 2.0.0-dev.1" $StableNotes (
        "a major Promotion composes accumulated dev.N fragments"
    )
    Assert-Contains "First accumulated fragment." $StableNotes (
        "the stable draft keeps the first accumulated fragment body"
    )
    Assert-Contains "### 2.0.0-dev.2" $StableNotes (
        "the stable draft keeps the second accumulated fragment heading"
    )
    Assert-Contains "Second accumulated fragment." $StableNotes (
        "the stable draft keeps the second accumulated fragment body"
    )
    Assert-Contains "### 2.0.0-dev.3" $StableNotes (
        "the stable draft includes the major-closing fragment"
    )
    Assert-Contains "This development fragment advances the Release line to ``2.0.0-dev.3`` on the way to stable ``2.0.0``." $StableNotes (
        "the stable draft includes the new fragment text"
    )
    Assert-Equal "chore(release): promote Release line to 2.0.0" (
        (& git -C $Scratch log -1 --format=%s)
    ) "a stable cut is committed as a Promotion rather than an advance"
    $MajorCommitPaths = Get-HeadCommitPaths -RepositoryRoot $Scratch
    Assert-Equal $true (
        $MajorCommitPaths -contains "docs/releases/v2.0.0-dev.3.md"
    ) "the Promotion commit includes the major fragment note path"
    Assert-Equal $true (
        $MajorCommitPaths -contains "docs/releases/v2.0.0.md"
    ) "the Promotion commit includes the composed stable note path"
    $PreserveFragmentScratch = New-ReleaseLineScratchRepository `
        -ScratchRoot $ScratchRoot `
        -Version "1.3.0-dev.2" `
        -ReachableStableTag "1.2.3"
    $AuthoredFragment = "# git-loopy 2.0.0-dev.3`n`nAn authored final development fragment.`n"
    Set-Utf8Text `
        -Path (Join-Path $PreserveFragmentScratch "docs/releases/v2.0.0-dev.3.md") `
        -Content $AuthoredFragment
    Import-Module $ModulePath -Force
    $PreservedFragmentMajor = Invoke-GitLoopyRepositoryReleaseLineAdvance `
        -RepositoryRoot $PreserveFragmentScratch `
        -Labels @("semver:major")
    Assert-Equal "2.0.0" $PreservedFragmentMajor.Version (
        "a Promotion cuts stable with an authored current development fragment"
    )
    Assert-Equal $AuthoredFragment (
        Get-Utf8Text -Path (Join-Path $PreserveFragmentScratch "docs/releases/v2.0.0-dev.3.md")
    ) "a Promotion preserves an authored current development fragment"
    Assert-Contains "An authored final development fragment." (
        Get-Utf8Text -Path (Join-Path $PreserveFragmentScratch "docs/releases/v2.0.0.md")
    ) "the stable draft composes the authored current development fragment"
    Assert-Equal $true (
        (Get-HeadCommitPaths -RepositoryRoot $PreserveFragmentScratch) -contains "docs/releases/v2.0.0-dev.3.md"
    ) "the Promotion commits its authored current development fragment"
    $PostPromotion = Invoke-GitLoopyRepositoryReleaseLineAdvance `
        -RepositoryRoot $Scratch -Labels @("semver:patch")
    Assert-Equal "2.0.1-dev.1" $PostPromotion.Version (
        "the issue after a major Promotion starts a fresh dev.N counter"
    )
    $ReleaseCommitCount = [int](& git -C $Scratch rev-list --count HEAD)
    Assert-Equal $null (
        Invoke-GitLoopyRepositoryReleaseLineAdvance `
            -RepositoryRoot $Scratch -Labels @("semver:none")
    ) "semver:none does not advance the Release line"
    Assert-Equal $ReleaseCommitCount ([int](& git -C $Scratch rev-list --count HEAD)) (
        "semver:none does not create a Release commit"
    )

    $PreserveScratch = New-ReleaseLineScratchRepository `
        -ScratchRoot $ScratchRoot `
        -Version "1.3.0-dev.2" `
        -ReachableStableTag "1.2.3"
    Set-Utf8Text `
        -Path (Join-Path $PreserveScratch "docs/releases/v2.0.0.md") `
        -Content "# git-loopy 2.0.0`n`nHuman-authored stable notes.`n"
    Import-Module $ModulePath -Force
    $PreservedMajor = Invoke-GitLoopyRepositoryReleaseLineAdvance `
        -RepositoryRoot $PreserveScratch `
        -Labels @("semver:major")
    Assert-Equal "2.0.0" $PreservedMajor.Version (
        "a major Promotion still cuts stable when a human-authored draft exists"
    )
    Assert-Equal "# git-loopy 2.0.0`n`nHuman-authored stable notes.`n" (
        Get-Utf8Text -Path (Join-Path $PreserveScratch "docs/releases/v2.0.0.md")
    ) "a major Promotion preserves an existing stable note"
    $PreserveCommitPaths = Get-HeadCommitPaths -RepositoryRoot $PreserveScratch
    Assert-Equal $true (
        $PreserveCommitPaths -contains "docs/releases/v2.0.0-dev.3.md"
    ) "the preserved Promotion still commits its fragment note path"
    Assert-Equal $true (
        $PreserveCommitPaths -contains "docs/releases/v2.0.0.md"
    ) "the preserved Promotion commits the human stable note path"
}
finally {
    if ([IO.Directory]::Exists($ScratchRoot)) {
        Remove-Item -LiteralPath $ScratchRoot -Recurse -Force -ErrorAction Stop
    }
}

Write-Output "PowerShell Release-line conformance: ok"
