"""Tests for the GitHub Actions Lane-contribution worker (#460)."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_loopy import github_actions_worker as worker
from git_loopy.skill_install import installed_catalog_dir


def _request(*, source_kind: str = "packaged") -> dict[str, object]:
    return {
        "skill_policy": {
            "enabled": ["code-review"],
            "required": ["code-review"],
            "legacy_denied": [],
            "source_kinds": {"code-review": source_kind},
            "base_scope": "global",
            "fallback": None,
        }
    }


def _install_skill(root: Path) -> None:
    skill = root / "code-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: review code\n---\n",
        encoding="utf-8",
    )


def test_worker_rebuilds_the_exact_closed_world_skill_exposure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _install_skill(installed_catalog_dir(worker.os.environ))

    exposure = worker._skill_exposure(_request(), tmp_path / "exposure")

    assert exposure.policy.enabled == ("code-review",)
    assert exposure.policy.required == ("code-review",)
    assert exposure.disabled_skills == ()
    assert (Path(exposure.skill_directories[0]) / "code-review" / "SKILL.md").is_file()


def test_worker_refuses_a_skill_the_pinned_catalog_cannot_supply(tmp_path: Path) -> None:
    with pytest.raises(
        worker.WorkerRequestError, match="only installed-catalog Skills"
    ):
        worker._skill_exposure(_request(source_kind="personal"), tmp_path / "exposure")
