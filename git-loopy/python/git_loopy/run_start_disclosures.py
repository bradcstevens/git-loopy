"""Once-per-Run disclosures that do not create a host-compute meter (#463)."""

from __future__ import annotations

from typing import Literal

__all__ = ["run_start_disclosures"]

_HostMeteringState = Literal["not_applicable", "free", "metered"]


def run_start_disclosures(
    *,
    host_placement: str,
    repository_visibility: str | None,
    inside_ci: bool,
) -> dict[str, dict[str, str | bool | None]]:
    """Project the Run-start facts about host billing and publishing identity."""
    metering_state, metering_message = _host_metering(
        host_placement, repository_visibility
    )
    disclosures: dict[str, dict[str, str | bool | None]] = {
        "host_metering": {
            "state": metering_state,
            "message": metering_message,
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
