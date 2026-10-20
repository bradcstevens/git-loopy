"""Once-per-Run disclosures that do not create a host-compute meter (#463)."""

from __future__ import annotations

from typing import Literal

from git_loopy.host_yield import HostYieldState

__all__ = ["run_start_disclosures"]

_HostMeteringState = Literal["not_applicable", "free", "metered"]

#: What each **Host yield policy** disclosure state means to an operator.
#: Keyed by the state :func:`~git_loopy.host_yield.host_yield_disclosure`
#: resolves, so the message and the state can never disagree.
_HOST_YIELD_MESSAGES: dict[HostYieldState, str] = {
    "utility": (
        "This Run yields to your foreground work: it and every process it "
        "starts run under the macOS utility QoS clamp."
    ),
    "none": (
        "The Host yield policy is off by operator request, so this Run "
        "competes with your foreground work at normal priority."
    ),
    "unsupported": (
        "This platform declares no Host yield policy, so this Run makes no "
        "yield guarantee."
    ),
}


def run_start_disclosures(
    *,
    host_placement: str,
    repository_visibility: str | None,
    inside_ci: bool,
    host_yield: HostYieldState,
    remote_residence: bool = False,
) -> dict[str, dict[str, str | bool | None]]:
    """Project the Run-start facts about host billing, host yield and identity."""
    metering_state, metering_message = _host_metering(
        host_placement, repository_visibility
    )
    if remote_residence:
        metering_state = "metered"
        metering_message = (
            "Remote Run compute is metered to the Azure subscription. "
            "Read the provider's charges in Azure billing."
        )
    disclosures: dict[str, dict[str, str | bool | None]] = {
        "host_metering": {
            "state": metering_state,
            "message": metering_message,
        },
        "host_yield": {
            "state": host_yield,
            "message": _host_yield_message(host_yield),
        },
    }
    if inside_ci:
        disclosures["ci_trigger_identity"] = {
            "can_trigger_downstream_ci": False,
            "message": (
                "This Run is publishing from CI, so its identity cannot trigger "
                "downstream CI. git-loopy never selects, transports, or upgrades "
                "a credential to change that behavior."
            ),
        }
    return disclosures


def _host_yield_message(state: HostYieldState) -> str:
    """Refuse a state outside the contract's closed vocabulary (#817).

    The parameter is typed, so a caller that passed its resolved Config value
    instead of the disclosure — the collapse #817 exists to prevent — fails the
    type check. The runtime guard still stands for an untyped caller.
    """
    try:
        return _HOST_YIELD_MESSAGES[state]
    except KeyError:
        raise ValueError(
            f"host_yield must be one of "
            f"{sorted(_HOST_YIELD_MESSAGES)} (got {state!r})"
        ) from None


def _host_metering(
    host_placement: str, repository_visibility: str | None
) -> tuple[_HostMeteringState, str]:
    if host_placement == "local":
        return (
            "not_applicable",
            "Host compute metering is not applicable: Lane contributions run "
            "on the local Execution host.",
        )
    if repository_visibility == "PUBLIC":
        return (
            "free",
            "The standard GitHub Actions runner for Lane contributions is free "
            "on this public target repository.",
        )
    return (
        "metered",
        "Dispatching Lane contributions spends GitHub Actions minutes billed "
        "to the target repository's owner. git-loopy does not report that "
        "spend; read it in GitHub Actions billing.",
    )
