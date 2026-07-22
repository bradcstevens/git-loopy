# PROTOTYPE: Can native PowerShell preserve the trusted publish ordering needed
# for one immutable Producer revision to become Ready guidance?

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Transition {
    param(
        [Parameter(Mandatory)]
        [Collections.IDictionary]$State,
        [Parameter(Mandatory)]
        [string]$Action
    )

    $Next = [ordered]@{}
    foreach ($Entry in $State.GetEnumerator()) {
        $Next[$Entry.Key] = $Entry.Value
    }

    switch ($Action) {
        "publish" {
            if (-not $Next.durable_transition_evidence) {
                $Next.last_result = "rejected: transition evidence is not durable"
                break
            }
            $Next.index_established = $true
            $Next.producer_revision_appended = $true
            $Next.producer_revision_reread = $true
            $Next.last_result = "committed"
        }
        "reconcile" {
            $Committed = (
                $Next.producer_revision_appended -and
                $Next.producer_revision_reread
            )
            $Next.guidance = if ($Committed -and $Next.target_open) {
                "Ready"
            }
            else {
                "Waiting"
            }
            $Next.last_result = "observed"
        }
        "close" {
            $Next.target_open = $false
            $Next.last_result = "target closed"
        }
        default {
            $Next.last_result = "unknown action"
        }
    }
    return $Next
}

$State = [ordered]@{
    durable_transition_evidence = $true
    index_established = $false
    producer_revision_appended = $false
    producer_revision_reread = $false
    target_open = $true
    guidance = "Waiting"
    last_result = "initial"
}

while ($true) {
    Clear-Host
    $State | ConvertTo-Json
    $Action = Read-Host "[publish/reconcile/close/quit]"
    if ($Action -ceq "quit") {
        break
    }
    $State = Invoke-Transition -State $State -Action $Action
}
