"""Tests for the Run-start host-metering and CI-identity disclosures (#463)."""

from __future__ import annotations

from git_loopy.run_start_disclosures import run_start_disclosures


def test_a_local_host_says_host_metering_is_not_applicable() -> None:
    disclosures = run_start_disclosures(
        host_placement="local",
        repository_visibility=None,
        inside_ci=False,
    )

    assert disclosures["host_metering"] == {
        "state": "not_applicable",
        "message": (
            "Host compute metering is not applicable: Lane contributions run "
            "on the local Execution host."
        ),
    }


def test_a_public_target_says_the_standard_actions_runner_is_free() -> None:
    disclosures = run_start_disclosures(
        host_placement="github-actions",
        repository_visibility="PUBLIC",
        inside_ci=False,
    )

    assert disclosures["host_metering"] == {
        "state": "free",
        "message": (
            "The standard GitHub Actions runner for Lane contributions is free "
            "on this public target repository."
        ),
    }


def test_a_private_target_discloses_unreported_actions_spend() -> None:
    disclosures = run_start_disclosures(
        host_placement="github-actions",
        repository_visibility="PRIVATE",
        inside_ci=False,
    )

    assert disclosures["host_metering"] == {
        "state": "metered",
        "message": (
            "Dispatching Lane contributions spends GitHub Actions minutes billed "
            "to the target repository's owner. git-loopy does not report that "
            "spend; read it in GitHub Actions billing."
        ),
    }


def test_a_ci_run_discloses_its_non_triggering_publishing_identity() -> None:
    disclosures = run_start_disclosures(
        host_placement="local",
        repository_visibility=None,
        inside_ci=True,
    )

    assert disclosures["ci_trigger_identity"] == {
        "can_trigger_downstream_ci": False,
        "message": (
            "This Run is publishing from CI, so its identity cannot trigger "
            "downstream CI. git-loopy never selects, transports, or upgrades a "
            "credential to change that behavior."
        ),
    }


def test_a_non_ci_run_omits_the_ci_trigger_identity_disclosure() -> None:
    disclosures = run_start_disclosures(
        host_placement="local",
        repository_visibility=None,
        inside_ci=False,
    )

    assert "ci_trigger_identity" not in disclosures
