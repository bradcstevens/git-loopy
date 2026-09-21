# PROTOTYPE — THROWAWAY. Not production, not gated, not dot-sourced by anything.
#
# Question (ADR-0033 slice 5): can PowerShell run a Lease renewer that does not
# outlive a killed parent? PowerShell offers two shapes and they differ exactly
# where it matters: Start-Job runs the renewer in a SEPARATE pwsh process (which
# a killed parent orphans, so the Lease never expires), while Start-ThreadJob
# runs it in-process (so it cannot outlive its owner). Same API shape, opposite
# crash-recovery behaviour.

param(
    [Parameter(Mandatory)][ValidateSet("process", "thread")][string]$Mode,
    [Parameter(Mandatory)][string]$BeatFile,
    [double]$Interval = 1
)

$Body = {
    param($BeatFile, $Interval)
    while ($true) {
        Start-Sleep -Seconds $Interval
        Add-Content -Path $BeatFile -Value "beat $([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
    }
}

if ($Mode -eq "process") {
    $Job = Start-Job -ScriptBlock $Body -ArgumentList $BeatFile, $Interval
}
else {
    if (-not (Get-Module -ListAvailable -Name ThreadJob -ErrorAction SilentlyContinue) -and
        -not (Get-Command Start-ThreadJob -ErrorAction SilentlyContinue)) {
        Write-Output "UNAVAILABLE: Start-ThreadJob not present"
        exit 3
    }
    $Job = Start-ThreadJob -ScriptBlock $Body -ArgumentList $BeatFile, $Interval
}

Write-Output "renewer started ($Mode) job=$($Job.Id)"
# The long, silent agent session. The driver SIGKILLs this process.
while ($true) { Start-Sleep -Seconds 3600 }
