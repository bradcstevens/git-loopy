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

foreach ($Case in Get-PowerShellCases "major_promotion_cases") {
    $Advanced = Invoke-GitLoopyReleaseLineAdvance `
        -LastStableVersion $Case["last_stable_version"] `
        -CurrentTarget $Case["current_target"] `
        -CurrentCounter $Case["current_counter"] `
        -BumpClass $Case["bump_class"]
    Assert-Equal $Case["resulting_version"] (
        (Invoke-GitLoopyMajorReleaseLinePromotion -ReleaseLine $Advanced).Version
    ) "major Bump-class Promotion: $($Case["id"])"
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
$Scratch = Join-Path ([IO.Path]::GetTempPath()) ("git-loopy-release-" + [guid]::NewGuid())
try {
    [IO.Directory]::CreateDirectory($Scratch) | Out-Null
    $VersionPaths = @(
        "VERSION",
        "git-loopy/python/git_loopy/__init__.py",
        "git-loopy/python/git_loopy/VERSION",
        "git-loopy/python/pyproject.toml",
        "git-loopy/python/uv.lock",
        "git-loopy/tui/Cargo.toml",
        "git-loopy/tui/Cargo.lock",
        "git-loopy/tui/README.md"
    )
    foreach ($Path in $VersionPaths) {
        $Destination = Join-Path $Scratch $Path
        [IO.Directory]::CreateDirectory((Split-Path -Parent $Destination)) | Out-Null
        [IO.File]::Copy((Join-Path $RepositoryRoot $Path), $Destination)
    }
    Set-GitLoopyRepositoryReleaseVersion -RepositoryRoot $Scratch -Version "1.2.3"
    & git -C $Scratch init -q
    & git -C $Scratch config user.email tester@example.invalid
    & git -C $Scratch config user.name "Test Runner"
    & git -C $Scratch add -A
    & git -C $Scratch commit -qm "initial Release metadata"

    $Advance = Invoke-GitLoopyRepositoryReleaseLineAdvance `
        -RepositoryRoot $Scratch -Labels @("semver:patch")
    Assert-Equal "patch" $Advance.BumpClass "a closed patch resolves its Bump class"
    Assert-Equal "1.2.4" $Advance.Target "a closed patch ratchets its target"
    Assert-Equal "1.2.4-dev.1" $Advance.Version (
        "a closed patch advances every Release metadata copy"
    )
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

    $SecondAdvance = Invoke-GitLoopyRepositoryReleaseLineAdvance `
        -RepositoryRoot $Scratch -Labels @("semver:minor")
    Assert-Equal "1.3.0-dev.2" $SecondAdvance.Version (
        "a second closure preserves the running Release-line counter"
    )
    Assert-Equal "chore(release): advance Release line to 1.3.0-dev.2" (
        (& git -C $Scratch log -1 --format=%s)
    ) "the Release line is committed after every metadata copy changes"
    $Major = Invoke-GitLoopyRepositoryReleaseLineAdvance `
        -RepositoryRoot $Scratch -Labels @("semver:major")
    Assert-Equal "2.0.0" $Major.Version (
        "a major Bump class cuts stable without a milestone"
    )
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
}
finally {
    if ([IO.Directory]::Exists($Scratch)) {
        [IO.Directory]::Delete($Scratch, $true)
    }
}

Write-Output "PowerShell Release-line conformance: ok"
