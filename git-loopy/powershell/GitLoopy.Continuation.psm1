Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Script:CapabilityManifest = [ordered]@{
    continuation_contract_versions = @("1.0")
    record_formats = @(1)
    wrapper_contract_version = "1.2"
    event_schema_version = "1.1"
    tracker_adapters = [ordered]@{
        github = [ordered]@{ operations = @() }
    }
    operations = [ordered]@{
        capabilities = $true
        publish = $false
        reconcile = $false
        "record-dispatch-result" = $false
        "repair-index" = $false
    }
    instruction_handlers = @()
    instruction_modes = @()
    evaluators = @()
    effect_scopes = @()
    optional_capabilities = [ordered]@{
        terminal_rendering = $false
        concurrent_dispatch = $false
    }
    continuation_modes = [ordered]@{
        default = "off"
        off = $true
        report = $false
        "execute-frontier" = $false
    }
}

function Get-GitLoopyContinuationUsage {
    [CmdletBinding()]
    param()
    return @"
Usage: git-loopy.ps1 continuation <operation> [options]

Operations:
  capabilities
  publish [--input FILE]
  reconcile [--input FILE] [--terminal]
  record-dispatch-result [--input FILE]
  repair-index [--input FILE]
"@
}

function Write-GitLoopyContinuationJson {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$Value
    )
    [Console]::Out.WriteLine(
        ($Value | ConvertTo-Json -Compress -Depth 20)
    )
}

function Write-GitLoopyContinuationError {
    param(
        [Parameter(Mandatory)]
        [string]$Operation,
        [Parameter(Mandatory)]
        [string]$Code,
        [Parameter(Mandatory)]
        [string]$Message
    )
    Write-GitLoopyContinuationJson ([ordered]@{
        ok = $false
        operation = $Operation
        error = [ordered]@{
            code = $Code
            message = $Message
        }
    })
    [Console]::Error.WriteLine("git-loopy continuation: $Message")
    return 1
}

function Read-GitLoopyContinuationRequest {
    param([AllowNull()][object]$InputPath)

    $Bytes = if ($null -ne $InputPath) {
        try {
            [IO.File]::ReadAllBytes($InputPath)
        }
        catch {
            throw "could not read request: $($_.Exception.Message)"
        }
    }
    else {
        $Memory = [IO.MemoryStream]::new()
        try {
            [Console]::OpenStandardInput().CopyTo($Memory)
            $Memory.ToArray()
        }
        finally {
            $Memory.Dispose()
        }
    }

    try {
        $Encoding = [Text.UTF8Encoding]::new($false, $true)
        $Text = $Encoding.GetString($Bytes)
        $Request = $Text | ConvertFrom-Json -AsHashtable
    }
    catch {
        throw "request must be one UTF-8 JSON object"
    }
    if ($Request -isnot [Collections.IDictionary]) {
        throw "request must be one UTF-8 JSON object"
    }
    return $Request
}

function Invoke-GitLoopyContinuationMain {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [string[]]$Arguments
    )

    if ($Arguments.Count -eq 0) {
        [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
        return 2
    }
    $Operation = $Arguments[0]
    if ($Operation -ceq "capabilities") {
        if ($Arguments.Count -ne 1) {
            [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
            return 2
        }
        Write-GitLoopyContinuationJson ([ordered]@{
            ok = $true
            capabilities = $Script:CapabilityManifest
        })
        return 0
    }

    $SupportedSurface = @(
        "publish",
        "reconcile",
        "record-dispatch-result",
        "repair-index"
    )
    if ($Operation -cnotin $SupportedSurface) {
        [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
        return 2
    }

    $InputPath = $null
    $Terminal = $false
    for ($Index = 1; $Index -lt $Arguments.Count; $Index++) {
        $Argument = $Arguments[$Index]
        if ($Argument -ceq "--input") {
            $Index++
            if (
                $Index -ge $Arguments.Count -or
                $Arguments[$Index].StartsWith("-", [StringComparison]::Ordinal) -or
                $null -ne $InputPath
            ) {
                [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
                return 2
            }
            $InputPath = $Arguments[$Index]
            continue
        }
        if ($Argument.StartsWith("--input=", [StringComparison]::Ordinal)) {
            $Value = $Argument.Substring("--input=".Length)
            if ([string]::IsNullOrEmpty($Value) -or $null -ne $InputPath) {
                [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
                return 2
            }
            $InputPath = $Value
            continue
        }
        if ($Argument -ceq "--terminal") {
            if ($Operation -cne "reconcile" -or $Terminal) {
                [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
                return 2
            }
            $Terminal = $true
            continue
        }
        [Console]::Error.WriteLine((Get-GitLoopyContinuationUsage))
        return 2
    }

    try {
        Read-GitLoopyContinuationRequest -InputPath $InputPath | Out-Null
    }
    catch {
        return Write-GitLoopyContinuationError `
            -Operation $Operation `
            -Code "invalid_request" `
            -Message $_.Exception.Message
    }

    return Write-GitLoopyContinuationError `
        -Operation $Operation `
        -Code "unsupported_operation" `
        -Message "$Operation is not supported by this distribution"
}

Export-ModuleMember -Function @(
    "Get-GitLoopyContinuationUsage",
    "Invoke-GitLoopyContinuationMain"
)
