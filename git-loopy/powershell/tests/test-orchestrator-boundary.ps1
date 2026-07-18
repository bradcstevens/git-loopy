Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7+ is required (found $($PSVersionTable.PSVersion))."
}

$PortDir = Split-Path -Parent $PSScriptRoot
$Entrypoint = Join-Path $PortDir "git-loopy.ps1"
$Pwsh = (
    Get-Command pwsh -CommandType Application |
        Select-Object -First 1
).Source

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
        [Parameter(Mandatory)]
        [string]$Text,
        [Parameter(Mandatory)]
        [string]$Needle,
        [Parameter(Mandatory)]
        [string]$Description
    )

    if (-not $Text.Contains($Needle, [StringComparison]::Ordinal)) {
        throw "FAIL: $Description`nmissing: $Needle`nactual:  $Text"
    }
}

function Write-FakeCommand {
    param(
        [Parameter(Mandatory)]
        [string]$BinDir,
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$Body
    )

    if ($IsWindows) {
        $ScriptPath = Join-Path $BinDir "$Name-fake.ps1"
        $LauncherPath = Join-Path $BinDir "$Name.cmd"
        [IO.File]::WriteAllText(
            $ScriptPath,
            $Body,
            [Text.UTF8Encoding]::new($false)
        )
        $Launcher = "@pwsh -NoLogo -NoProfile -File `"%~dp0$Name-fake.ps1`" %*`r`n"
        [IO.File]::WriteAllText(
            $LauncherPath,
            $Launcher,
            [Text.ASCIIEncoding]::new()
        )
        return
    }

    $CommandPath = Join-Path $BinDir $Name
    $Script = "#!/usr/bin/env pwsh`n" + $Body
    [IO.File]::WriteAllText(
        $CommandPath,
        $Script,
        [Text.UTF8Encoding]::new($false)
    )
    & chmod +x $CommandPath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not make fake $Name executable."
    }
}

function Write-FakeTools {
    param(
        [Parameter(Mandatory)]
        [string]$BinDir
    )

    [IO.Directory]::CreateDirectory($BinDir) | Out-Null
    Write-FakeCommand -BinDir $BinDir -Name "git" -Body @'
$ErrorActionPreference = "Stop"
if (($args -join " ") -ceq "rev-parse --show-toplevel") {
    [Console]::Out.WriteLine($env:FAKE_REPO_ROOT)
    exit 0
}
[Console]::Error.WriteLine("unexpected git invocation: " + ($args -join " "))
exit 90
'@
    Write-FakeCommand -BinDir $BinDir -Name "copilot" -Body @'
[Console]::Error.WriteLine("copilot must not run in the discovery slice")
exit 91
'@
    Write-FakeCommand -BinDir $BinDir -Name "gh" -Body @'
$ErrorActionPreference = "Stop"
[IO.File]::AppendAllText(
    $env:FAKE_GH_LOG,
    ($args -join " ") + [Environment]::NewLine
)
$Command = if ($args.Count -ge 2) { "$($args[0]) $($args[1])" } else { "" }
switch -CaseSensitive ($Command) {
    "auth status" {
        exit $(if ($env:FAKE_GH_AUTH_STATUS) {
            [int]$env:FAKE_GH_AUTH_STATUS
        } else {
            0
        })
    }
    "repo view" {
        [Console]::Out.WriteLine(
            '{"owner":{"login":"example"},"name":"repo","defaultBranchRef":{"name":"main"}}'
        )
        exit 0
    }
    "issue list" {
        $Count = if ([IO.File]::Exists($env:FAKE_GH_LIST_COUNT)) {
            [int][IO.File]::ReadAllText($env:FAKE_GH_LIST_COUNT)
        } else {
            0
        }
        [IO.File]::WriteAllText(
            $env:FAKE_GH_LIST_COUNT,
            [string]($Count + 1)
        )
        [Console]::Out.Write([IO.File]::ReadAllText($env:FAKE_GH_LIST_JSON))
        exit 0
    }
    "issue view" {
        $ViewPath = Join-Path $env:FAKE_GH_VIEW_DIR "$($args[2]).json"
        [Console]::Out.Write([IO.File]::ReadAllText($ViewPath))
        exit 0
    }
    default {
        [Console]::Error.WriteLine(
            "unexpected gh invocation: " + ($args -join " ")
        )
        exit 92
    }
}
'@
}

function New-TestRepo {
    param(
        [Parameter(Mandatory)]
        [string]$Root
    )

    [IO.Directory]::CreateDirectory((Join-Path $Root "docs/agents")) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $Root "git-loopy")) | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $Root "docs/agents/issue-tracker.md"),
        "# Issue tracker`n"
    )
    [IO.File]::WriteAllText(
        (Join-Path $Root "git-loopy/PROMPT.md"),
        "# Project prompt`n"
    )
}

function Invoke-Entrypoint {
    param(
        [Parameter(Mandatory)]
        [string]$Repo,
        [Parameter(Mandatory)]
        [string]$FakeBin,
        [Parameter(Mandatory)]
        [string]$StdoutPath,
        [Parameter(Mandatory)]
        [string]$StderrPath,
        [string[]]$Arguments = @()
    )

    $OldPath = $env:PATH
    $OldHome = $env:HOME
    $OldXdg = $env:XDG_CONFIG_HOME
    $OldRepoRoot = $env:FAKE_REPO_ROOT
    $OriginalLocation = Get-Location
    try {
        $env:PATH = $FakeBin + [IO.Path]::PathSeparator + $OldPath
        $env:HOME = Join-Path $Repo "home"
        $env:XDG_CONFIG_HOME = Join-Path $Repo "xdg"
        $env:FAKE_REPO_ROOT = $Repo
        Set-Location $Repo
        & $Pwsh `
            -NoLogo `
            -NoProfile `
            -File $Entrypoint `
            @Arguments 1> $StdoutPath 2> $StderrPath
        return $LASTEXITCODE
    }
    finally {
        Set-Location $OriginalLocation
        $env:PATH = $OldPath
        $env:HOME = $OldHome
        $env:XDG_CONFIG_HOME = $OldXdg
        $env:FAKE_REPO_ROOT = $OldRepoRoot
    }
}

function Read-Events {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    return @(
        [IO.File]::ReadAllLines($Path) |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ | ConvertFrom-Json -AsHashtable }
    )
}

$TempDir = Join-Path ([IO.Path]::GetTempPath()) (
    "git-loopy-pwsh-boundary-$([guid]::NewGuid())"
)
[IO.Directory]::CreateDirectory($TempDir) | Out-Null

try {
    $HelpStdout = Join-Path $TempDir "help.stdout"
    $HelpStderr = Join-Path $TempDir "help.stderr"
    & $Pwsh `
        -NoLogo `
        -NoProfile `
        -File $Entrypoint `
        --help 1> $HelpStdout 2> $HelpStderr
    Assert-Equal 0 $LASTEXITCODE "help exit"
    Assert-Contains (
        [IO.File]::ReadAllText($HelpStdout)
    ) "Usage:" "help stdout"
    Assert-Equal 0 (
        [IO.File]::ReadAllText($HelpStderr).Length
    ) "help wrote to stderr"

    $EmptyRepo = Join-Path $TempDir "empty"
    $EmptyBin = Join-Path $TempDir "empty-bin"
    New-TestRepo -Root $EmptyRepo
    Write-FakeTools -BinDir $EmptyBin
    $EmptyList = Join-Path $TempDir "empty-list.json"
    $EmptyViews = Join-Path $TempDir "empty-views"
    [IO.File]::WriteAllText($EmptyList, "[]`n")
    [IO.Directory]::CreateDirectory($EmptyViews) | Out-Null
    $env:FAKE_GH_LOG = Join-Path $TempDir "empty-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "empty-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews

    $EmptyStdout = Join-Path $TempDir "empty.stdout"
    $EmptyStderr = Join-Path $TempDir "empty.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $EmptyRepo `
        -FakeBin $EmptyBin `
        -StdoutPath $EmptyStdout `
        -StderrPath $EmptyStderr
    Assert-Equal 0 $Status "empty GitHub Pool exit"

    $Events = Read-Events -Path $EmptyStdout
    Assert-Equal (
        "wrapper.run.start,wrapper.iteration.start," +
        "wrapper.afk_ready.collected,wrapper.iteration.end,wrapper.run.end"
    ) ([string]::Join(",", @($Events | ForEach-Object { $_["type"] }))) (
        "empty-Pool event sequence"
    )
    Assert-Equal "github" $Events[0]["issue_source"] "Run issue source"
    Assert-Equal 0 $Events[2]["issues"].Count "empty collected Pool"
    Assert-Equal "empty_pool" $Events[4]["outcome"] "empty Run outcome"
    Assert-Equal 1 $Events[4]["iterations_run"] "empty Run Iteration count"

    $ReplayFiles = @(
        Get-ChildItem `
            -LiteralPath (Join-Path $EmptyRepo ".git-loopy/logs") `
            -Filter "*.jsonl" `
            -File
    )
    Assert-Equal 1 $ReplayFiles.Count "empty Run replay file count"
    Assert-Equal (
        [IO.File]::ReadAllText($EmptyStdout)
    ) ([IO.File]::ReadAllText($ReplayFiles[0].FullName)) (
        "empty Run stream and replay parity"
    )
    $GhLog = [IO.File]::ReadAllText($env:FAKE_GH_LOG)
    Assert-Contains $GhLog "auth status" "GitHub auth preflight"
    Assert-Contains $GhLog "repo view" "GitHub repo preflight"
    Assert-Contains $GhLog "issue list" "GitHub Pool collection"

    $env:GIT_LOOPY_MODEL = "env-model"
    $env:GIT_LOOPY_REASONING_EFFORT = "high"
    $env:GIT_LOOPY_ISSUE_SOURCE = "prds"
    $env:GIT_LOOPY_MAX_NMT_STRIKES = "7"
    $env:GIT_LOOPY_DENY_TOOLS = "env-tool"
    $env:GIT_LOOPY_DENY_SKILLS = "env-skill"
    $env:GIT_LOOPY_SEND_TIMEOUT_SECONDS = "90"
    $EnvStdout = Join-Path $TempDir "env.stdout"
    $EnvStderr = Join-Path $TempDir "env.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $EmptyRepo `
        -FakeBin $EmptyBin `
        -StdoutPath $EnvStdout `
        -StderrPath $EnvStderr
    foreach ($Name in @(
        "GIT_LOOPY_MODEL",
        "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_ISSUE_SOURCE",
        "GIT_LOOPY_MAX_NMT_STRIKES",
        "GIT_LOOPY_DENY_TOOLS",
        "GIT_LOOPY_DENY_SKILLS",
        "GIT_LOOPY_SEND_TIMEOUT_SECONDS"
    )) {
        [Environment]::SetEnvironmentVariable($Name, $null)
    }
    Assert-Equal 0 $Status "environment-only Run exit"
    $EnvEvents = Read-Events -Path $EnvStdout
    Assert-Equal "env-model" $EnvEvents[0]["model"] "environment model"
    Assert-Equal "high" (
        $EnvEvents[0]["reasoning_effort"]
    ) "environment reasoning effort"
    Assert-Equal "prds" $EnvEvents[0]["issue_source"] "environment issue source"
    Assert-Equal 7 (
        $EnvEvents[0]["max_nmt_strikes"]
    ) "environment Strike threshold"
    Assert-Equal "env-tool" (
        [string]::Join(",", $EnvEvents[0]["deny_tools"])
    ) "environment tool denylist"
    Assert-Equal "env-skill" (
        [string]::Join(",", $EnvEvents[0]["deny_skills"])
    ) "environment skill denylist"
    Assert-Equal 90.0 (
        $EnvEvents[0]["send_timeout_seconds"]
    ) "environment send timeout"

    $CapRepo = Join-Path $TempDir "github-cap"
    $CapBin = Join-Path $TempDir "github-cap-bin"
    New-TestRepo -Root $CapRepo
    Write-FakeTools -BinDir $CapBin
    $CapList = Join-Path $TempDir "github-list.json"
    [IO.File]::WriteAllText($CapList, @'
[
  {
    "number": 41,
    "title": "Eligible",
    "body": "## What to build\nShip it.\n\n## Acceptance criteria\n- Done.",
    "labels": [{"name": "ready-for-agent"}],
    "state": "OPEN",
    "url": "https://example.invalid/issues/41"
  },
  {
    "number": 42,
    "title": "Bare planning issue",
    "body": "No required headings.",
    "labels": [{"name": "ready-for-agent"}],
    "state": "OPEN",
    "url": "https://example.invalid/issues/42"
  }
]
'@)
    $CapViews = Join-Path $TempDir "github-views"
    [IO.Directory]::CreateDirectory($CapViews) | Out-Null
    [IO.File]::WriteAllText((Join-Path $CapViews "41.json"), @'
{
  "number": 41,
  "title": "Eligible",
  "body": "## What to build\nShip it.\n\n## Acceptance criteria\n- Done.",
  "labels": [{"name": "ready-for-agent"}],
  "state": "OPEN",
  "url": "https://example.invalid/issues/41",
  "comments": []
}
'@)
    $env:FAKE_GH_LOG = Join-Path $TempDir "github-cap-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "github-cap-list.count"
    $env:FAKE_GH_LIST_JSON = $CapList
    $env:FAKE_GH_VIEW_DIR = $CapViews

    $CapStdout = Join-Path $TempDir "github-cap.stdout"
    $CapStderr = Join-Path $TempDir "github-cap.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $CapRepo `
        -FakeBin $CapBin `
        -StdoutPath $CapStdout `
        -StderrPath $CapStderr `
        -Arguments @("2")
    Assert-Equal 0 $Status "bounded discovery Run exit"
    Assert-Equal "2" (
        [IO.File]::ReadAllText($env:FAKE_GH_LIST_COUNT)
    ) "Pool is rebuilt each Iteration"
    $CapEvents = Read-Events -Path $CapStdout
    $CollectionEvents = @(
        $CapEvents |
            Where-Object { $_["type"] -ceq "wrapper.afk_ready.collected" }
    )
    Assert-Equal 2 $CollectionEvents.Count "collection event count"
    foreach ($Event in $CollectionEvents) {
        Assert-Equal "41" (
            [string]::Join(",", $Event["issues"])
        ) "filtered Pool refs"
    }
    Assert-Equal "iteration_cap" $CapEvents[-1]["outcome"] "bounded Run outcome"
    Assert-Equal 2 $CapEvents[-1]["iterations_run"] "bounded Iteration count"
    Assert-True (
        -not ([IO.File]::ReadAllText($env:FAKE_GH_LOG)).Contains(
            "issue view 42 ",
            [StringComparison]::Ordinal
        )
    ) "ineligible issue was enriched after the cheap discriminator pass"

    [IO.File]::Delete($env:FAKE_GH_LIST_COUNT)
    $DefaultStdout = Join-Path $TempDir "github-default.stdout"
    $DefaultStderr = Join-Path $TempDir "github-default.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $CapRepo `
        -FakeBin $CapBin `
        -StdoutPath $DefaultStdout `
        -StderrPath $DefaultStderr
    Assert-Equal 0 $Status "default discovery Run exit"
    Assert-Equal "1" (
        [IO.File]::ReadAllText($env:FAKE_GH_LIST_COUNT)
    ) "default discovery count"
    $DefaultEvents = Read-Events -Path $DefaultStdout
    Assert-Equal "pool_discovered" (
        $DefaultEvents[-1]["outcome"]
    ) "default non-empty discovery outcome"
    Assert-Equal 1 (
        $DefaultEvents[-1]["iterations_run"]
    ) "default discovery Iteration count"

    $PrdsRepo = Join-Path $TempDir "prds"
    $PrdsBin = Join-Path $TempDir "prds-bin"
    New-TestRepo -Root $PrdsRepo
    Write-FakeTools -BinDir $PrdsBin
    $FeatureDir = Join-Path $PrdsRepo "prds/feature"
    $AlphaDir = Join-Path $PrdsRepo "prds/alpha"
    $AlphaBetaDir = Join-Path $PrdsRepo "prds/alpha-beta"
    [IO.Directory]::CreateDirectory((Join-Path $FeatureDir "done")) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $AlphaDir "done")) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $AlphaBetaDir "done")) | Out-Null
    $OutsidePrds = Join-Path $TempDir "outside-prds"
    [IO.Directory]::CreateDirectory($OutsidePrds) | Out-Null
    [IO.File]::WriteAllText((Join-Path $AlphaDir "001-ready.md"), @'
## What to build
Ship alpha.

## Acceptance criteria
- Done.
'@)
    [IO.File]::WriteAllText((Join-Path $AlphaBetaDir "001-ready.md"), @'
## What to build
Ship alpha-beta.

## Acceptance criteria
- Done.
'@)
    [IO.File]::WriteAllText((Join-Path $FeatureDir "001-ready.md"), @'
## What to build
Ship it.

## Acceptance criteria
- Done.
'@)
    [IO.File]::WriteAllText(
        (Join-Path $FeatureDir "002-bare.md"),
        "No required headings.`n"
    )
    [IO.File]::WriteAllText((Join-Path $OutsidePrds "004-escaped.md"), @'
## What to build
Read outside the worktree.

## Acceptance criteria
- Escaped.
'@)
    if (-not $IsWindows) {
        New-Item `
            -ItemType SymbolicLink `
            -Path (Join-Path $PrdsRepo "prds/escaped") `
            -Target $OutsidePrds | Out-Null
    }
    [IO.File]::WriteAllText((Join-Path $FeatureDir "done/003-archived.md"), @'
## What to build
Old work.

## Acceptance criteria
- Archived.
'@)
    $env:FAKE_GH_LOG = Join-Path $TempDir "prds-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "prds-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews
    $env:GIT_LOOPY_ISSUE_SOURCE = "github"

    $PrdsStdout = Join-Path $TempDir "prds.stdout"
    $PrdsStderr = Join-Path $TempDir "prds.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $PrdsRepo `
        -FakeBin $PrdsBin `
        -StdoutPath $PrdsStdout `
        -StderrPath $PrdsStderr `
        -Arguments @("1", "--issue-source", "prds")
    $env:GIT_LOOPY_ISSUE_SOURCE = $null
    Assert-Equal 0 $Status "local-PRD discovery exit"
    $PrdsEvents = Read-Events -Path $PrdsStdout
    Assert-Equal "prds" $PrdsEvents[0]["issue_source"] "CLI source precedence"
    $PrdsCollected = @(
        $PrdsEvents |
            Where-Object { $_["type"] -ceq "wrapper.afk_ready.collected" }
    )[0]
    Assert-Equal (
        "prds/alpha-beta/001-ready.md," +
        "prds/alpha/001-ready.md," +
        "prds/feature/001-ready.md"
    ) (
        [string]::Join(",", $PrdsCollected["issues"])
    ) "local-PRD discriminator"
    Assert-Equal "iteration_cap" $PrdsEvents[-1]["outcome"] "PRDs cap outcome"
    Assert-True (
        -not [IO.File]::Exists($env:FAKE_GH_LOG)
    ) "PRDs mode invoked gh"

    $LinkedRootRepo = Join-Path $TempDir "prds-root-link"
    $LinkedRootBin = Join-Path $TempDir "prds-root-link-bin"
    New-TestRepo -Root $LinkedRootRepo
    Write-FakeTools -BinDir $LinkedRootBin
    $OutsideRoot = Join-Path $TempDir "outside-prds-root"
    $OutsideFeature = Join-Path $OutsideRoot "feature"
    [IO.Directory]::CreateDirectory($OutsideFeature) | Out-Null
    [IO.File]::WriteAllText((Join-Path $OutsideFeature "001-escaped.md"), @'
## What to build
Read outside the worktree.

## Acceptance criteria
- Escaped.
'@)
    $LinkType = if ($IsWindows) { "Junction" } else { "SymbolicLink" }
    New-Item `
        -ItemType $LinkType `
        -Path (Join-Path $LinkedRootRepo "prds") `
        -Target $OutsideRoot | Out-Null
    $env:FAKE_GH_LOG = Join-Path $TempDir "prds-root-link-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "prds-root-link-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews

    $LinkedRootStdout = Join-Path $TempDir "prds-root-link.stdout"
    $LinkedRootStderr = Join-Path $TempDir "prds-root-link.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $LinkedRootRepo `
        -FakeBin $LinkedRootBin `
        -StdoutPath $LinkedRootStdout `
        -StderrPath $LinkedRootStderr `
        -Arguments @("1", "--issue-source", "prds")
    Assert-Equal 0 $Status "linked-PRD-root Run exit"
    $LinkedRootEvents = Read-Events -Path $LinkedRootStdout
    $LinkedRootCollected = @(
        $LinkedRootEvents |
            Where-Object { $_["type"] -ceq "wrapper.afk_ready.collected" }
    )[0]
    Assert-Equal 0 (
        $LinkedRootCollected["issues"].Count
    ) "local-PRD collection followed a linked root outside the worktree"
    Assert-Equal "empty_pool" (
        $LinkedRootEvents[-1]["outcome"]
    ) "linked-PRD-root outcome"
    Assert-Contains (
        [IO.File]::ReadAllText($LinkedRootStderr)
    ) "linked prds root is not allowed" "linked local-PRD root warning"

    $MissingRepo = Join-Path $TempDir "missing-tracker"
    $MissingBin = Join-Path $TempDir "missing-tracker-bin"
    [IO.Directory]::CreateDirectory((Join-Path $MissingRepo "git-loopy")) |
        Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $MissingRepo "git-loopy/PROMPT.md"),
        "# Prompt`n"
    )
    Write-FakeTools -BinDir $MissingBin
    $env:FAKE_GH_LOG = Join-Path $TempDir "missing-tracker-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "missing-tracker-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews

    $MissingStdout = Join-Path $TempDir "missing-tracker.stdout"
    $MissingStderr = Join-Path $TempDir "missing-tracker.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $MissingRepo `
        -FakeBin $MissingBin `
        -StdoutPath $MissingStdout `
        -StderrPath $MissingStderr
    Assert-Equal 1 $Status "missing issue-tracker configuration exit"
    Assert-Contains (
        [IO.File]::ReadAllText($MissingStderr)
    ) "/setup-agent-skills" "missing setup guidance"
    Assert-Equal 0 (
        [IO.File]::ReadAllText($MissingStdout).Length
    ) "preflight failure emitted Iteration work"
    Assert-True (
        -not [IO.File]::Exists($env:FAKE_GH_LOG)
    ) "preflight continued after missing tracker"

    $AuthRepo = Join-Path $TempDir "auth-failure"
    $AuthBin = Join-Path $TempDir "auth-failure-bin"
    New-TestRepo -Root $AuthRepo
    Write-FakeTools -BinDir $AuthBin
    $env:FAKE_GH_LOG = Join-Path $TempDir "auth-failure-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "auth-failure-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews
    $env:FAKE_GH_AUTH_STATUS = "1"

    $AuthStdout = Join-Path $TempDir "auth-failure.stdout"
    $AuthStderr = Join-Path $TempDir "auth-failure.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $AuthRepo `
        -FakeBin $AuthBin `
        -StdoutPath $AuthStdout `
        -StderrPath $AuthStderr
    $env:FAKE_GH_AUTH_STATUS = $null
    Assert-Equal 1 $Status "GitHub authentication preflight exit"
    Assert-Contains (
        [IO.File]::ReadAllText($AuthStderr)
    ) "gh auth login" "GitHub authentication guidance"
    Assert-Equal 0 (
        [IO.File]::ReadAllText($AuthStdout).Length
    ) "authentication failure emitted Run events"
    Assert-True (
        -not ([IO.File]::ReadAllText($env:FAKE_GH_LOG)).Contains(
            "issue list ",
            [StringComparison]::Ordinal
        )
    ) "authentication failure reached Pool collection"

    $UsageRepo = Join-Path $TempDir "usage"
    $UsageBin = Join-Path $TempDir "usage-bin"
    New-TestRepo -Root $UsageRepo
    Write-FakeTools -BinDir $UsageBin
    $env:FAKE_GH_LOG = Join-Path $TempDir "usage-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "usage-list.count"
    $env:FAKE_GH_LIST_JSON = $EmptyList
    $env:FAKE_GH_VIEW_DIR = $EmptyViews

    $UsageStdout = Join-Path $TempDir "usage.stdout"
    $UsageStderr = Join-Path $TempDir "usage.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $UsageRepo `
        -FakeBin $UsageBin `
        -StdoutPath $UsageStdout `
        -StderrPath $UsageStderr `
        -Arguments @("nope")
    Assert-Equal 2 $Status "malformed invocation exit"
    Assert-Equal 0 (
        [IO.File]::ReadAllText($UsageStdout).Length
    ) "usage error emitted Run events"
    Assert-True (
        -not [IO.File]::Exists($env:FAKE_GH_LOG)
    ) "usage error reached preflight"
}
finally {
    foreach ($Name in @(
        "FAKE_GH_LOG",
        "FAKE_GH_LIST_COUNT",
        "FAKE_GH_LIST_JSON",
        "FAKE_GH_VIEW_DIR",
        "FAKE_GH_AUTH_STATUS",
        "GIT_LOOPY_MODEL",
        "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_ISSUE_SOURCE",
        "GIT_LOOPY_MAX_NMT_STRIKES",
        "GIT_LOOPY_DENY_TOOLS",
        "GIT_LOOPY_DENY_SKILLS",
        "GIT_LOOPY_SEND_TIMEOUT_SECONDS"
    )) {
        [Environment]::SetEnvironmentVariable($Name, $null)
    }
    if ([IO.Directory]::Exists($TempDir)) {
        [IO.Directory]::Delete($TempDir, $true)
    }
}

Write-Output "PowerShell Orchestrator boundary: ok"
