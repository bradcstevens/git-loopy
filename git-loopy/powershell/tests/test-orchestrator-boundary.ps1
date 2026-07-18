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

# Turn scenarios exercise the real Copilot turn, so they run against a real git
# repository (real HEAD, commits_between, and recent-commit rendering) with only
# `gh` and `copilot` faked. `Write-TurnTools` deliberately ships no fake `git`,
# and the entry point keeps the inherited PATH so the real `git` resolves.
function Write-TurnTools {
    param(
        [Parameter(Mandatory)]
        [string]$BinDir
    )

    [IO.Directory]::CreateDirectory($BinDir) | Out-Null
    Write-FakeCommand -BinDir $BinDir -Name "copilot" -Body @'
$ErrorActionPreference = "Stop"
$Prompt = ""
$Capture = $false
$Flags = [Collections.Generic.List[string]]::new()
foreach ($Arg in $args) {
    if ($Capture) {
        $Prompt = [string]$Arg
        $Capture = $false
        continue
    }
    if ([string]$Arg -ceq "-p") {
        $Capture = $true
        continue
    }
    $Flags.Add([string]$Arg)
}
[IO.File]::WriteAllText(
    $env:FAKE_COPILOT_FLAGS,
    ($Flags -join "`n") + "`n"
)
[IO.File]::WriteAllText($env:FAKE_COPILOT_PROMPT, $Prompt)
$Calls = 0
if ([IO.File]::Exists($env:FAKE_COPILOT_CALLS)) {
    $Calls = [int][IO.File]::ReadAllText($env:FAKE_COPILOT_CALLS)
}
[IO.File]::WriteAllText($env:FAKE_COPILOT_CALLS, [string]($Calls + 1))
# Emit on stdout to prove the agent stream is routed away from the JSONL Event
# stream (the Orchestrator forwards it to stderr).
[Console]::Out.WriteLine("copilot agent stream marker")
$Commits = if ($env:FAKE_COPILOT_COMMITS) { [int]$env:FAKE_COPILOT_COMMITS } else { 0 }
for ($i = 0; $i -lt $Commits; $i++) {
    & git commit -q --allow-empty -m "agent: work $($i + 1)"
}
exit $(if ($env:FAKE_COPILOT_EXIT) { [int]$env:FAKE_COPILOT_EXIT } else { 0 })
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
        $Count = $Count + 1
        [IO.File]::WriteAllText($env:FAKE_GH_LIST_COUNT, [string]$Count)
        if ($env:FAKE_GH_EMPTY_AFTER -and
            ($Count -gt [int]$env:FAKE_GH_EMPTY_AFTER)) {
            [Console]::Out.WriteLine("[]")
        } else {
            [Console]::Out.Write([IO.File]::ReadAllText($env:FAKE_GH_LIST_JSON))
        }
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

function New-RealTestRepo {
    param(
        [Parameter(Mandatory)]
        [string]$Root
    )

    New-TestRepo -Root $Root
    & git -C $Root init -q
    & git -C $Root config user.email "tester@example.invalid"
    & git -C $Root config user.name "Test Runner"
    & git -C $Root commit -q --allow-empty -m "initial commit"
}

function Set-CopilotEnv {
    param(
        [Parameter(Mandatory)]
        [string]$Prefix
    )

    foreach ($Suffix in @("flags", "prompt", "calls")) {
        $Path = Join-Path $TempDir "$Prefix-copilot.$Suffix"
        if ([IO.File]::Exists($Path)) {
            [IO.File]::Delete($Path)
        }
    }
    $env:FAKE_COPILOT_FLAGS = Join-Path $TempDir "$Prefix-copilot.flags"
    $env:FAKE_COPILOT_PROMPT = Join-Path $TempDir "$Prefix-copilot.prompt"
    $env:FAKE_COPILOT_CALLS = Join-Path $TempDir "$Prefix-copilot.calls"
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
    New-RealTestRepo -Root $CapRepo
    Write-TurnTools -BinDir $CapBin
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
  "comments": [
    {
      "author": "maintainer",
      "body": "please prioritise",
      "createdAt": "2026-03-01T00:00:00Z"
    }
  ]
}
'@)
    $env:FAKE_GH_LOG = Join-Path $TempDir "github-cap-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "github-cap-list.count"
    $env:FAKE_GH_LIST_JSON = $CapList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    Set-CopilotEnv -Prefix "github-cap"
    $env:FAKE_COPILOT_COMMITS = "0"
    $env:GIT_LOOPY_MODEL = "env-model"
    $env:GIT_LOOPY_REASONING_EFFORT = "medium"
    $env:GIT_LOOPY_DENY_TOOLS = "env-tool"
    $env:GIT_LOOPY_DENY_SKILLS = "env-skill"

    $CapStdout = Join-Path $TempDir "github-cap.stdout"
    $CapStderr = Join-Path $TempDir "github-cap.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $CapRepo `
        -FakeBin $CapBin `
        -StdoutPath $CapStdout `
        -StderrPath $CapStderr `
        -Arguments @(
            "2",
            "--model", "cli-model",
            "--deny-tool", "cli-tool",
            "--deny-skill", "cli-skill"
        )
    foreach ($Name in @(
        "GIT_LOOPY_MODEL",
        "GIT_LOOPY_REASONING_EFFORT",
        "GIT_LOOPY_DENY_TOOLS",
        "GIT_LOOPY_DENY_SKILLS",
        "FAKE_COPILOT_COMMITS"
    )) {
        [Environment]::SetEnvironmentVariable($Name, $null)
    }
    Assert-Equal 0 $Status "bounded turn Run exit"
    Assert-Equal "2" (
        [IO.File]::ReadAllText($env:FAKE_GH_LIST_COUNT)
    ) "Pool is rebuilt each Iteration"
    Assert-Equal "2" (
        [IO.File]::ReadAllText($env:FAKE_COPILOT_CALLS)
    ) "exactly one Copilot turn per non-empty Iteration"
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
    Assert-Equal 0 (
        @(
            $CapEvents |
                Where-Object { $_["type"] -ceq "wrapper.commit.recorded" }
        ).Count
    ) "a turn with no new commits records no commit events"
    Assert-True (
        -not ([IO.File]::ReadAllText($env:FAKE_GH_LOG)).Contains(
            "issue view 42 ",
            [StringComparison]::Ordinal
        )
    ) "ineligible issue was enriched after the cheap discriminator pass"

    # The Iteration assembled the Python-reference minimum context: last-5
    # commits, the filtered Pool block (with recent comments), and the prompt.
    $CapPrompt = [IO.File]::ReadAllText($env:FAKE_COPILOT_PROMPT)
    Assert-Contains $CapPrompt "Previous commits: " "prompt carries the commits prefix"
    Assert-Contains $CapPrompt "initial commit" "prompt carries recent commit subjects"
    Assert-Contains $CapPrompt (
        "=== Issue #41: Eligible [labels: ready-for-agent] ==="
    ) "prompt carries the filtered issue block"
    Assert-Contains $CapPrompt (
        "--- Recent comments (newest first, up to 5) ---"
    ) "prompt carries recent comments"
    Assert-Contains $CapPrompt (
        "[2026-03-01T00:00:00Z @maintainer] please prioritise"
    ) "recent comments keep the raw ISO timestamp (no locale datetime coercion)"
    Assert-Contains $CapPrompt "please prioritise" "prompt carries comment bodies"
    Assert-Contains $CapPrompt "# Project prompt" (
        "prompt carries the resolved shared prompt"
    )

    # Resolved settings honor CLI-over-environment-over-default precedence.
    $CapFlags = [IO.File]::ReadAllText($env:FAKE_COPILOT_FLAGS)
    Assert-Contains $CapFlags "--yolo" "turn passes --yolo"
    Assert-Contains $CapFlags "--no-color" "turn streams without color"
    $CapFlagLines = [IO.File]::ReadAllLines($env:FAKE_COPILOT_FLAGS)
    Assert-True (
        $CapFlagLines -ccontains "cli-model"
    ) "CLI --model overrode the environment model"
    Assert-True (
        -not ($CapFlagLines -ccontains "env-model")
    ) "environment model leaked past the CLI override"
    Assert-True (
        $CapFlagLines -ccontains "medium"
    ) "environment reasoning effort was forwarded"
    Assert-True (
        $CapFlagLines -ccontains "cli-tool"
    ) "CLI deny-tool forwarded"
    Assert-True (
        $CapFlagLines -ccontains "env-tool"
    ) "environment deny-tool forwarded"
    Assert-True (
        $CapFlagLines -ccontains "skill(cli-skill)"
    ) "CLI deny-skill mapped onto --deny-tool skill(...)"
    Assert-True (
        $CapFlagLines -ccontains "skill(env-skill)"
    ) "environment deny-skill mapped onto --deny-tool skill(...)"

    # The agent's own output streams to stderr, never onto the Event stream.
    Assert-Contains (
        [IO.File]::ReadAllText($CapStderr)
    ) "copilot agent stream marker" "agent output streams to stderr"
    Assert-True (
        -not ([IO.File]::ReadAllText($CapStdout)).Contains(
            "copilot agent stream marker",
            [StringComparison]::Ordinal
        )
    ) "agent output polluted the JSONL Event stream"
    $CapReplay = @(
        Get-ChildItem `
            -LiteralPath (Join-Path $CapRepo ".git-loopy/logs") `
            -Filter "*.jsonl" `
            -File
    )
    Assert-Equal 1 $CapReplay.Count "turn Run replay file count"
    Assert-Equal (
        [IO.File]::ReadAllText($CapStdout)
    ) ([IO.File]::ReadAllText($CapReplay[0].FullName)) (
        "turn Run stream and replay parity"
    )

    [IO.File]::Delete($env:FAKE_GH_LIST_COUNT)
    Set-CopilotEnv -Prefix "github-default"
    $env:FAKE_COPILOT_COMMITS = "0"
    $env:FAKE_GH_EMPTY_AFTER = "1"
    $DefaultStdout = Join-Path $TempDir "github-default.stdout"
    $DefaultStderr = Join-Path $TempDir "github-default.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $CapRepo `
        -FakeBin $CapBin `
        -StdoutPath $DefaultStdout `
        -StderrPath $DefaultStderr
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_COMMITS", $null)
    [Environment]::SetEnvironmentVariable("FAKE_GH_EMPTY_AFTER", $null)
    Assert-Equal 0 $Status "unlimited turn Run exit"
    Assert-Equal "2" (
        [IO.File]::ReadAllText($env:FAKE_GH_LIST_COUNT)
    ) "unlimited Run rebuilds the Pool until it empties"
    Assert-Equal "1" (
        [IO.File]::ReadAllText($env:FAKE_COPILOT_CALLS)
    ) "unlimited Run runs one turn before its Pool empties"
    $DefaultEvents = Read-Events -Path $DefaultStdout
    $DefaultCollected = @(
        $DefaultEvents |
            Where-Object { $_["type"] -ceq "wrapper.afk_ready.collected" } |
            ForEach-Object { [string]::Join(",", $_["issues"]) }
    )
    Assert-Equal "41;" ([string]::Join(";", $DefaultCollected)) (
        "unlimited Run rebuilds then empties the Pool"
    )
    Assert-Equal "empty_pool" (
        $DefaultEvents[-1]["outcome"]
    ) "unlimited turn Run terminates on an empty Pool"
    Assert-Equal 2 (
        $DefaultEvents[-1]["iterations_run"]
    ) "unlimited Run Iteration count"

    # A turn that produces new commits records one commit event per commit, in
    # git's newest-first order, and only closes the Iteration afterwards.
    $CommitsRepo = Join-Path $TempDir "agent-commits"
    $CommitsBin = Join-Path $TempDir "agent-commits-bin"
    New-RealTestRepo -Root $CommitsRepo
    Write-TurnTools -BinDir $CommitsBin
    $CommitsList = Join-Path $TempDir "agent-commits-list.json"
    [IO.File]::Copy($CapList, $CommitsList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "agent-commits-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "agent-commits-list.count"
    $env:FAKE_GH_LIST_JSON = $CommitsList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    Set-CopilotEnv -Prefix "agent-commits"
    $env:FAKE_COPILOT_COMMITS = "2"
    $CommitsStdout = Join-Path $TempDir "agent-commits.stdout"
    $CommitsStderr = Join-Path $TempDir "agent-commits.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $CommitsRepo `
        -FakeBin $CommitsBin `
        -StdoutPath $CommitsStdout `
        -StderrPath $CommitsStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_COMMITS", $null)
    Assert-Equal 0 $Status "agent-commit turn Run exit"
    $CommitsEvents = Read-Events -Path $CommitsStdout
    Assert-Equal (
        "wrapper.run.start,wrapper.iteration.start," +
        "wrapper.afk_ready.collected,wrapper.commit.recorded," +
        "wrapper.commit.recorded,wrapper.iteration.end,wrapper.run.end"
    ) ([string]::Join(",", @($CommitsEvents | ForEach-Object { $_["type"] }))) (
        "commit events precede the Iteration end that closes their Iteration"
    )
    $RecordedCommits = @(
        $CommitsEvents |
            Where-Object { $_["type"] -ceq "wrapper.commit.recorded" }
    )
    Assert-Equal "agent: work 2,agent: work 1" (
        [string]::Join(",", @($RecordedCommits | ForEach-Object { $_["subject"] }))
    ) "new commits are recorded newest-first"
    foreach ($Commit in $RecordedCommits) {
        Assert-True (
            $Commit.Contains("sha") -and
            $Commit.Contains("subject") -and
            $Commit.Contains("date")
        ) "commit event carries the contract payload keys"
        Assert-True (
            [string]$Commit["sha"] -match '^[0-9a-f]{40}$'
        ) "commit event carries a full SHA"
        Assert-True (
            [string]$Commit["date"] -match '^\d{4}-\d{2}-\d{2}$'
        ) "commit event carries an ISO date"
    }
    Assert-Equal "iteration_cap" (
        $CommitsEvents[-1]["outcome"]
    ) "agent-commit Run outcome"

    # A non-zero agent process warns and the Run still finishes cleanly
    # (warn-and-continue); the turn's real exit status is preserved.
    $NonZeroRepo = Join-Path $TempDir "agent-nonzero"
    $NonZeroBin = Join-Path $TempDir "agent-nonzero-bin"
    New-RealTestRepo -Root $NonZeroRepo
    Write-TurnTools -BinDir $NonZeroBin
    $NonZeroList = Join-Path $TempDir "agent-nonzero-list.json"
    [IO.File]::Copy($CapList, $NonZeroList, $true)
    $env:FAKE_GH_LOG = Join-Path $TempDir "agent-nonzero-gh.log"
    $env:FAKE_GH_LIST_COUNT = Join-Path $TempDir "agent-nonzero-list.count"
    $env:FAKE_GH_LIST_JSON = $NonZeroList
    $env:FAKE_GH_VIEW_DIR = $CapViews
    Set-CopilotEnv -Prefix "agent-nonzero"
    $env:FAKE_COPILOT_COMMITS = "0"
    $env:FAKE_COPILOT_EXIT = "7"
    $NonZeroStdout = Join-Path $TempDir "agent-nonzero.stdout"
    $NonZeroStderr = Join-Path $TempDir "agent-nonzero.stderr"
    $Status = Invoke-Entrypoint `
        -Repo $NonZeroRepo `
        -FakeBin $NonZeroBin `
        -StdoutPath $NonZeroStdout `
        -StderrPath $NonZeroStderr `
        -Arguments @("1")
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_COMMITS", $null)
    [Environment]::SetEnvironmentVariable("FAKE_COPILOT_EXIT", $null)
    Assert-Equal 0 $Status "non-zero agent turn must not fail the Run"
    Assert-Equal "1" (
        [IO.File]::ReadAllText($env:FAKE_COPILOT_CALLS)
    ) "the turn ran despite its non-zero exit"
    Assert-Contains (
        [IO.File]::ReadAllText($NonZeroStderr)
    ) "copilot turn exited with status 7" "non-zero agent exit warns to stderr"
    $NonZeroEvents = Read-Events -Path $NonZeroStdout
    Assert-Equal 0 (
        @(
            $NonZeroEvents |
                Where-Object { $_["type"] -ceq "wrapper.commit.recorded" }
        ).Count
    ) "a non-zero turn with no commits records no commit events"
    Assert-Equal "iteration_cap" (
        $NonZeroEvents[-1]["outcome"]
    ) "non-zero agent turn stayed on warn-and-continue"

    $PrdsRepo = Join-Path $TempDir "prds"
    $PrdsBin = Join-Path $TempDir "prds-bin"
    New-RealTestRepo -Root $PrdsRepo
    Write-TurnTools -BinDir $PrdsBin
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
    Set-CopilotEnv -Prefix "prds"

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
    # The local-PRD turn assembled the same minimum context: the resolved prompt
    # plus the PRD block rendered from its worktree-relative reference.
    Assert-Equal "1" (
        [IO.File]::ReadAllText($env:FAKE_COPILOT_CALLS)
    ) "local-PRD Iteration ran exactly one turn"
    $PrdsPrompt = [IO.File]::ReadAllText($env:FAKE_COPILOT_PROMPT)
    Assert-Contains $PrdsPrompt (
        "=== prds/alpha-beta/001-ready.md ==="
    ) "prompt carries the local-PRD block"
    Assert-Contains $PrdsPrompt "Ship alpha-beta." "prompt carries the PRD body"
    Assert-Contains $PrdsPrompt "# Project prompt" (
        "prompt carries the resolved shared prompt"
    )

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
        "FAKE_GH_EMPTY_AFTER",
        "FAKE_COPILOT_FLAGS",
        "FAKE_COPILOT_PROMPT",
        "FAKE_COPILOT_CALLS",
        "FAKE_COPILOT_COMMITS",
        "FAKE_COPILOT_EXIT",
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
