Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7+ is required (found $($PSVersionTable.PSVersion))."
}

$PortDir = Split-Path -Parent $PSScriptRoot
$Entrypoint = Join-Path $PortDir "git-loopy.ps1"
$ScriptedGitHubPath = Join-Path $PSScriptRoot "ScriptedGitHub.ps1"
$FixturePath = Join-Path (
    Split-Path -Parent $PortDir
) "conformance/continuation-scenarios.json"
$Fixture = Get-Content -LiteralPath $FixturePath -Raw |
    ConvertFrom-Json -AsHashtable
$Pwsh = (
    Get-Command pwsh -CommandType Application |
        Select-Object -First 1
).Source
$TempRoot = Join-Path (
    [IO.Path]::GetTempPath()
) ("git-loopy-continuation-" + [Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($TempRoot) | Out-Null
$FakeBin = Join-Path $TempRoot "bin"
[IO.Directory]::CreateDirectory($FakeBin) | Out-Null
if ($IsWindows) {
    $FakeGh = Join-Path $FakeBin "gh.cmd"
    [IO.File]::WriteAllText(
        $FakeGh,
        "@echo off`r`n" +
            "`"$Pwsh`" -NoLogo -NoProfile -File " +
            "`"$ScriptedGitHubPath`" %*`r`n" +
            "exit /b %ERRORLEVEL%`r`n",
        [Text.ASCIIEncoding]::new()
    )
}
else {
    $FakeGh = Join-Path $FakeBin "gh"
    [IO.File]::WriteAllText(
        $FakeGh,
        "#!/bin/sh`nexec `"$Pwsh`" -NoLogo -NoProfile -File " +
            "`"$ScriptedGitHubPath`" `"`$@`"`n",
        [Text.UTF8Encoding]::new($false)
    )
    & chmod +x $FakeGh
    if ($LASTEXITCODE -ne 0) {
        throw "Could not make scripted gh transport executable."
    }
}

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

function Get-ConsumedSteps {
    param([Parameter(Mandatory)][string]$StatePath)

    if ([IO.File]::Exists($StatePath)) {
        return [int][IO.File]::ReadAllText($StatePath)
    }
    return 0
}

function Test-ScriptedGitHubTransport {
    $Probe = $Fixture["github_transport_probe"]
    $ScriptPath = Join-Path $TempRoot "probe-github-script.json"
    $StatePath = Join-Path $TempRoot "probe-github-state"
    $LogPath = Join-Path $TempRoot "probe-github-calls"
    [IO.File]::WriteAllText(
        $ScriptPath,
        (ConvertTo-Json -InputObject @($Probe["github_script"]) -Compress -Depth 50),
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllText($LogPath, "", [Text.UTF8Encoding]::new($false))
    [IO.File]::Delete($StatePath)

    foreach ($Invocation in $Probe["invocations"]) {
        $StartInfo = [Diagnostics.ProcessStartInfo]::new()
        $StartInfo.FileName = $Pwsh
        $StartInfo.UseShellExecute = $false
        $StartInfo.RedirectStandardInput = $true
        $StartInfo.RedirectStandardOutput = $true
        $StartInfo.RedirectStandardError = $true
        $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_LOG"] = $LogPath
        $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT"] = $ScriptPath
        $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_STATE"] = $StatePath
        foreach ($Argument in @(
            "-NoLogo",
            "-NoProfile",
            "-File",
            $ScriptedGitHubPath
        )) {
            $StartInfo.ArgumentList.Add($Argument)
        }
        foreach ($Argument in $Invocation["arguments"]) {
            $StartInfo.ArgumentList.Add($Argument)
        }

        $Process = [Diagnostics.Process]::new()
        $Process.StartInfo = $StartInfo
        Assert-True ($Process.Start()) "scripted GitHub probe process starts"
        $ProbeInput = if ($Invocation.Contains("stdin_json")) {
            $Invocation["stdin_json"] | ConvertTo-Json -Compress -Depth 50
        }
        else {
            [string]($Invocation["stdin"] ?? "")
        }
        $Process.StandardInput.Write($ProbeInput)
        $Process.StandardInput.Close()
        $Stdout = $Process.StandardOutput.ReadToEnd()
        $Stderr = $Process.StandardError.ReadToEnd()
        $Process.WaitForExit()

        $Expected = $Invocation["expected"]
        Assert-True (
            $Process.ExitCode -eq $Expected["exit_code"]
        ) "scripted GitHub probe exit code"
        if ($Expected.Contains("stdout_json")) {
            $ActualJson = $Stdout | ConvertFrom-Json -AsHashtable |
                ConvertTo-Json -Compress -Depth 50
            $ExpectedJson = $Expected["stdout_json"] |
                ConvertTo-Json -Compress -Depth 50
            Assert-True (
                $ActualJson -ceq $ExpectedJson
            ) "scripted GitHub probe JSON stdout"
        }
        else {
            Assert-True (
                $Stdout -ceq [string]$Expected["stdout"]
            ) "scripted GitHub probe stdout"
        }
        Assert-True (
            $Stderr.Contains(
                [string]$Expected["stderr_contains"],
                [StringComparison]::OrdinalIgnoreCase
            )
        ) "scripted GitHub probe stderr"
    }

    Assert-True (
        (Get-ConsumedSteps $StatePath) -eq @($Probe["github_script"]).Count
    ) "scripted GitHub probe consumed every listed call"
    $ActualCalls = @([IO.File]::ReadAllLines($LogPath))
    Assert-True (
        (
            $ActualCalls | ConvertTo-Json -Compress
        ) -ceq (
            @($Probe["expected_github_calls"]) | ConvertTo-Json -Compress
        )
    ) "scripted GitHub probe call log"
}

function Invoke-Scenario {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Scenario,
        [AllowNull()]
        [Collections.IDictionary]$Transport
    )
    $Arguments = [Collections.Generic.List[string]]::new()
    $InputFile = Join-Path $TempRoot "$($Scenario["id"])-request.json"
    $Request = $Scenario["request"]
    $RequestContent = ""
    if ($null -ne $Request) {
        if ($Request.Contains("base64")) {
            $RequestContent = ""
        }
        elseif ($Request.Contains("raw")) {
            $RequestContent = [string]$Request["raw"]
        }
        else {
            $RequestContent = $Request["json"] |
                ConvertTo-Json -Compress -Depth 20
        }
        if ($Request["source"] -ceq "file") {
            if ($Request.Contains("base64")) {
                [IO.File]::WriteAllBytes(
                    $InputFile,
                    [Convert]::FromBase64String($Request["base64"])
                )
            }
            else {
                [IO.File]::WriteAllText(
                    $InputFile,
                    $RequestContent,
                    [Text.UTF8Encoding]::new($false)
                )
            }
        }
    }
    foreach ($Argument in $Scenario["arguments"]) {
        $Arguments.Add(
            $(if ($Argument -ceq '$INPUT_FILE') { $InputFile } else { $Argument })
        )
    }

    if ($null -eq $Transport) {
        $GithubLog = Join-Path $TempRoot "$($Scenario["id"])-github.log"
        $ScriptPath = Join-Path $TempRoot "$($Scenario["id"])-github-script.json"
        $StatePath = Join-Path $TempRoot "$($Scenario["id"])-github-state"
        [IO.File]::WriteAllText(
            $ScriptPath,
            (
                ConvertTo-Json `
                    -InputObject @($Scenario["github_script"]) `
                    -Compress `
                    -Depth 50
            ),
            [Text.UTF8Encoding]::new($false)
        )
        [IO.File]::WriteAllText($GithubLog, "", [Text.UTF8Encoding]::new($false))
        [IO.File]::Delete($StatePath)
    }
    else {
        $GithubLog = $Transport["GithubLog"]
        $ScriptPath = $Transport["ScriptPath"]
        $StatePath = $Transport["StatePath"]
    }

    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Pwsh
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_LOG"] = $GithubLog
    $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT"] = $ScriptPath
    $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_STATE"] = $StatePath
    $StartInfo.Environment["PATH"] = (
        $FakeBin + [IO.Path]::PathSeparator + $env:PATH
    )
    foreach ($Argument in @("-NoLogo", "-NoProfile", "-File", $Entrypoint)) {
        $StartInfo.ArgumentList.Add($Argument)
    }
    foreach ($Argument in $Arguments) {
        $StartInfo.ArgumentList.Add($Argument)
    }

    $Process = [Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    Assert-True ($Process.Start()) "$($Scenario["id"]) process starts"
    if ($null -ne $Request -and $Request["source"] -ceq "stdin") {
        $Process.StandardInput.Write($RequestContent)
    }
    $Process.StandardInput.Close()
    $Stdout = $Process.StandardOutput.ReadToEnd()
    $Stderr = $Process.StandardError.ReadToEnd()
    $Process.WaitForExit()
    return [ordered]@{
        ExitCode = $Process.ExitCode
        Stdout = $Stdout
        Stderr = $Stderr
        GithubCalls = if ([IO.File]::Exists($GithubLog)) {
            @([IO.File]::ReadAllLines($GithubLog))
        }
        else {
            @()
        }
        ConsumedSteps = Get-ConsumedSteps $StatePath
    }
}

function Assert-ScenarioResult {
    param(
        [Parameter(Mandatory)]
        [string]$Id,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Result,
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Expected
    )

    Assert-True (
        $Result.ExitCode -eq $Expected["exit_code"]
    ) (
        "$Id exit code: expected $($Expected["exit_code"]), " +
        "got $($Result.ExitCode); stderr: $($Result.Stderr)"
    )

    if ($null -eq $Expected["stdout"]) {
        Assert-True (
            [string]::IsNullOrEmpty($Result.Stdout)
        ) "$Id writes no stdout"
    }
    elseif ($Expected.Contains("stdout_exact")) {
        Assert-True (
            $Result.Stdout -ceq [string]$Expected["stdout_exact"]
        ) "$Id exact stdout matches the shared fixture"
    }
    else {
        $ActualJson = $Result.Stdout |
            ConvertFrom-Json -AsHashtable |
            ConvertTo-Json -Compress -Depth 20
        $ExpectedJson = $Expected["stdout"] |
            ConvertTo-Json -Compress -Depth 20
        Assert-True (
            [Text.Json.Nodes.JsonNode]::DeepEquals(
                [Text.Json.Nodes.JsonNode]::Parse($ActualJson),
                [Text.Json.Nodes.JsonNode]::Parse($ExpectedJson)
            )
        ) (
            "$Id stdout matches the shared fixture; " +
            "expected: $ExpectedJson; actual: $ActualJson"
        )
        $Lines = @(
            $Result.Stdout -split "\r?\n" |
                Where-Object { $_.Length -gt 0 }
        )
        Assert-True (
            $Lines.Count -eq 1
        ) "$Id writes exactly one stdout object"
    }

    if ($Expected.Contains("stderr_exact")) {
        Assert-True (
            $Result.Stderr -ceq [string]$Expected["stderr_exact"]
        ) "$Id exact stderr matches the shared fixture"
    }
    elseif ($null -eq $Expected["stderr_contains"]) {
        Assert-True (
            [string]::IsNullOrEmpty($Result.Stderr)
        ) "$Id writes no stderr"
    }
    else {
        $Needle = [string]$Expected["stderr_contains"]
        Assert-True (
            $Result.Stderr.Contains($Needle, [StringComparison]::OrdinalIgnoreCase)
        ) "$Id stderr contains '$Needle'"
    }
}

function Test-GitHubFailureBoundaries {
    $Workflow = @(
        $Fixture["workflows"] |
            Where-Object { $_["id"] -ceq "trusted-planning-action" }
    )[0]
    $Cases = @(
        [ordered]@{
            id = "publish-github-failure"
            arguments = @("continuation", "publish")
            request = $Workflow["commands"][0]["request"]
            github_script = @(
                [ordered]@{
                    command = "api repos/octo/example/issues/comments/7001"
                    exit_code = 1
                    stdout = ""
                    stderr = "evidence unavailable"
                }
            )
            expected = [ordered]@{
                exit_code = 1
                stdout = [ordered]@{
                    ok = $false
                    operation = "publish"
                    error = [ordered]@{
                        code = "github_error"
                        message = (
                            "GitHub operation failed while reading " +
                            "transition evidence"
                        )
                    }
                }
                stderr_contains = "GitHub operation failed"
                github_calls = @(
                    "api repos/octo/example/issues/comments/7001"
                )
            }
        },
        [ordered]@{
            id = "reconcile-github-failure"
            arguments = @("continuation", "reconcile")
            request = $Workflow["commands"][1]["request"]
            github_script = @(
                [ordered]@{
                    command = (
                        "issue list --repo octo/example --state all " +
                        "--label git-loopy-continuation --limit 100 " +
                        "--json number,state,url,comments"
                    )
                    exit_code = 1
                    stdout = ""
                    stderr = "carrier discovery unavailable"
                }
            )
            expected = [ordered]@{
                exit_code = 1
                stdout = [ordered]@{
                    ok = $false
                    operation = "reconcile"
                    error = [ordered]@{
                        code = "github_error"
                        message = (
                            "GitHub operation failed while discovering " +
                            "indexed carriers"
                        )
                    }
                }
                stderr_contains = "GitHub operation failed"
                github_calls = @(
                    (
                        "issue list --repo octo/example --state all " +
                        "--label git-loopy-continuation --limit 100 " +
                        "--json number,state,url,comments"
                    )
                )
            }
        }
    )

    foreach ($Case in $Cases) {
        $Result = Invoke-Scenario -Scenario $Case
        Assert-ScenarioResult `
            -Id $Case["id"] `
            -Result $Result `
            -Expected $Case["expected"]
        Assert-True (
            (
                $Result.GithubCalls | ConvertTo-Json -Compress
            ) -ceq (
                @($Case["expected"]["github_calls"]) |
                    ConvertTo-Json -Compress
            )
        ) "$($Case["id"]) stops at the failed GitHub boundary"
        Assert-True (
            $Result.ConsumedSteps -eq @($Case["github_script"]).Count
        ) "$($Case["id"]) consumes the scripted failure"
    }
}

try {
    Test-ScriptedGitHubTransport
    Test-GitHubFailureBoundaries

    foreach ($Scenario in $Fixture["scenarios"]) {
        if (
            $Scenario.Contains("distributions") -and
            "powershell" -notin @($Scenario["distributions"])
        ) {
            continue
        }
        $Result = Invoke-Scenario -Scenario $Scenario
        $Expected = $Scenario["expected"]
        Assert-ScenarioResult `
            -Id $Scenario["id"] `
            -Result $Result `
            -Expected $Expected
        Assert-True (
            (
                $Result.GithubCalls | ConvertTo-Json -Compress
            ) -ceq (
                @($Expected["github_calls"]) | ConvertTo-Json -Compress
            )
        ) "$($Scenario["id"]) scripted GitHub calls match"
        Assert-True (
            $Result.ConsumedSteps -eq @($Scenario["github_script"]).Count
        ) "$($Scenario["id"]) consumed every scripted GitHub call"
    }

    foreach ($Workflow in $Fixture["workflows"]) {
        if ("powershell" -notin @($Workflow["distributions"])) {
            continue
        }
        $WorkflowId = $Workflow["id"]
        $Transport = [ordered]@{
            GithubLog = Join-Path $TempRoot "$WorkflowId-github.log"
            ScriptPath = Join-Path $TempRoot "$WorkflowId-github-script.json"
            StatePath = Join-Path $TempRoot "$WorkflowId-github-state"
        }
        [IO.File]::WriteAllText(
            $Transport.ScriptPath,
            (
                ConvertTo-Json `
                    -InputObject @($Workflow["github_script"]) `
                    -Compress `
                    -Depth 50
            ),
            [Text.UTF8Encoding]::new($false)
        )
        [IO.File]::WriteAllText(
            $Transport.GithubLog,
            "",
            [Text.UTF8Encoding]::new($false)
        )
        [IO.File]::Delete($Transport.StatePath)

        $CommandIndex = 0
        foreach ($Command in $Workflow["commands"]) {
            $CommandIndex++
            $Case = [ordered]@{
                id = "$WorkflowId-$CommandIndex"
                arguments = $Command["arguments"]
                request = $Command["request"]
            }
            $Result = Invoke-Scenario -Scenario $Case -Transport $Transport
            Assert-ScenarioResult `
                -Id $Case.id `
                -Result $Result `
                -Expected $Command["expected"]
        }

        $ActualCalls = @([IO.File]::ReadAllLines($Transport.GithubLog))
        Assert-True (
            (
                $ActualCalls | ConvertTo-Json -Compress
            ) -ceq (
                @($Workflow["expected_github_calls"]) |
                    ConvertTo-Json -Compress
            )
        ) "$WorkflowId scripted GitHub calls match"
        Assert-True (
            (Get-ConsumedSteps $Transport.StatePath) -eq
                @($Workflow["github_script"]).Count
        ) "$WorkflowId consumed every scripted GitHub call"
    }
}
finally {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

[Console]::Out.WriteLine("PowerShell Continuation conformance: ok")
