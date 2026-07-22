Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7+ is required (found $($PSVersionTable.PSVersion))."
}

$PortDir = Split-Path -Parent $PSScriptRoot
$Entrypoint = Join-Path $PortDir "git-loopy.ps1"
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
    [IO.File]::WriteAllText(
        (Join-Path $FakeBin "gh.cmd"),
        "@echo %*>>`"%GIT_LOOPY_SCRIPTED_GITHUB_LOG%`"`r`n@exit /b 97`r`n",
        [Text.ASCIIEncoding]::new()
    )
}
else {
    $FakeGh = Join-Path $FakeBin "gh"
    [IO.File]::WriteAllText(
        $FakeGh,
        "#!/bin/sh`nprintf '%s\n' `"`$*`" >>`"`$GIT_LOOPY_SCRIPTED_GITHUB_LOG`"`nexit 97`n",
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

function Convert-ExpectedValue {
    param([AllowNull()][object]$Value)
    if (
        $Value -is [Collections.IDictionary] -and
        $Value.Count -eq 1 -and
        $Value.Contains('$fixture') -and
        $Value['$fixture'] -ceq "capability_manifest"
    ) {
        return $Fixture["capability_manifest"]
    }
    if ($Value -is [Collections.IDictionary]) {
        $Result = [ordered]@{}
        foreach ($Key in $Value.Keys) {
            $Result[$Key] = Convert-ExpectedValue $Value[$Key]
        }
        return $Result
    }
    if ($Value -is [Collections.IList]) {
        return @($Value | ForEach-Object { Convert-ExpectedValue $_ })
    }
    return $Value
}

function Invoke-Scenario {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Scenario
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

    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Pwsh
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $GithubLog = Join-Path $TempRoot "$($Scenario["id"])-github.log"
    $StartInfo.Environment["GIT_LOOPY_SCRIPTED_GITHUB_LOG"] = $GithubLog
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
    }
}

try {
    foreach ($Scenario in $Fixture["scenarios"]) {
        $Result = Invoke-Scenario -Scenario $Scenario
        $Expected = $Scenario["expected"]
        Assert-True (
            $Result.ExitCode -eq $Expected["exit_code"]
        ) "$($Scenario["id"]) exit code"

        if ($null -eq $Expected["stdout"]) {
            Assert-True (
                [string]::IsNullOrEmpty($Result.Stdout)
            ) "$($Scenario["id"]) writes no stdout"
        }
        else {
            $ActualObject = $Result.Stdout | ConvertFrom-Json -AsHashtable
            $ExpectedObject = Convert-ExpectedValue $Expected["stdout"]
            $ActualJson = $ActualObject | ConvertTo-Json -Compress -Depth 20
            $ExpectedJson = $ExpectedObject | ConvertTo-Json -Compress -Depth 20
            Assert-True (
                $ActualJson -ceq $ExpectedJson
            ) "$($Scenario["id"]) stdout matches the shared fixture"
            $Lines = @(
                $Result.Stdout -split "\r?\n" |
                    Where-Object { $_.Length -gt 0 }
            )
            Assert-True (
                $Lines.Count -eq 1
            ) "$($Scenario["id"]) writes exactly one stdout object"
        }

        $Needle = $Expected["stderr_contains"]
        if ($null -eq $Needle) {
            Assert-True (
                [string]::IsNullOrEmpty($Result.Stderr)
            ) "$($Scenario["id"]) writes no stderr"
        }
        else {
            Assert-True (
                $Result.Stderr.Contains(
                    [string]$Needle,
                    [StringComparison]::OrdinalIgnoreCase
                )
            ) "$($Scenario["id"]) stderr contains '$Needle'"
        }
        Assert-True (
            (
                $Result.GithubCalls | ConvertTo-Json -Compress
            ) -ceq (
                @($Expected["github_calls"]) | ConvertTo-Json -Compress
            )
        ) "$($Scenario["id"]) scripted GitHub calls match"
    }
}
finally {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

[Console]::Out.WriteLine("PowerShell Continuation conformance: ok")
