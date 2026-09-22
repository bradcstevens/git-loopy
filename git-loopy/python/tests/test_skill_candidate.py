"""Judge a candidate Skill catalog before the pin moves (#633).

CI proves the pinned revision. These tests prove an operator can put a
caller-named checkout — including a working clone with uncommitted edits — in
front of the same cross-repo contracts, offline, without publishing anything
and without editing the pin.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_loopy.prompt import packaged_required_skills
from git_loopy.skill_source import PIN_PATH
from git_loopy.skill_candidate import (
    CONTRACT_LABEL_VOCABULARY,
    CONTRACT_REQUIRED_SKILL,
    CONTRACT_SKILL_REFERENCE,
    VERIFY_COMMAND,
    CandidateCheckoutError,
    main,
    verify_candidate,
)


def test_an_absent_path_fails_with_an_actionable_message(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-checkout"

    with pytest.raises(CandidateCheckoutError) as raised:
        verify_candidate(missing, repo_root=tmp_path)

    message = str(raised.value)
    assert str(missing) in message
    assert "does not exist" in message
    assert "network" in message


def test_a_directory_that_is_not_a_skills_checkout_names_why(
    tmp_path: Path,
) -> None:
    unrelated = tmp_path / "notes"
    unrelated.mkdir()
    (unrelated / "README.md").write_text("not a catalog\n", encoding="utf-8")

    with pytest.raises(CandidateCheckoutError) as raised:
        verify_candidate(unrelated, repo_root=tmp_path)

    message = str(raised.value)
    assert str(unrelated) in message
    assert "not a Skills checkout" in message
    assert "skills" in message


def _write_skill(skills: Path, name: str) -> None:
    skill = skills / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: The {name} Skill.\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_a_required_skill_that_is_only_a_symlink_is_absent(tmp_path: Path) -> None:
    """A symlink is not the Skill. The check must not follow it out of the checkout."""
    checkout = tmp_path / "candidate"
    skills = checkout / "skills"
    outside = tmp_path / "outside-skill"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: Not in the checkout.\n---\n",
        encoding="utf-8",
    )
    _write_skill(skills, "code-review")
    tdd = skills / "tdd"
    tdd.mkdir()
    (tdd / "SKILL.md").symlink_to(outside / "SKILL.md")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(
        "| [`/code-review`](https://github.com/bradcstevens/git-loopy-skills/tree/main/skills/code-review) | review |\n",
        encoding="utf-8",
    )

    verdict = verify_candidate(checkout, repo_root=repo)

    absent = [
        breach
        for breach in verdict.breaches
        if breach.contract == CONTRACT_REQUIRED_SKILL and breach.subject == "tdd"
    ]
    assert absent, f"a symlink must not satisfy the Required Skill, got {verdict.breaches}"


def test_a_candidate_missing_a_required_skill_names_that_skill(tmp_path: Path) -> None:
    checkout = tmp_path / "candidate"
    _write_skill(checkout / "skills", "code-review")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(
        "| [`/code-review`](https://github.com/bradcstevens/git-loopy-skills/tree/main/skills/code-review) | review |\n",
        encoding="utf-8",
    )

    verdict = verify_candidate(checkout, repo_root=repo)

    assert verdict.passed is False
    absent = [
        breach
        for breach in verdict.breaches
        if breach.contract == CONTRACT_REQUIRED_SKILL and breach.subject == "tdd"
    ]
    assert absent, f"expected the Required Skill tdd to be named, got {verdict.breaches}"
    assert "Required Skill absent: tdd" in str(absent[0])


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    raise AssertionError("repo root not found")


def test_a_broken_skill_reference_names_the_reference_that_does_not_resolve(
    tmp_path: Path,
) -> None:
    """A front-door Skill the candidate dropped is a dead route, not a skip."""
    checkout = tmp_path / "candidate"
    skills = checkout / "skills"
    for name in packaged_required_skills():
        _write_skill(skills, name)

    verdict = verify_candidate(checkout, repo_root=_repo_root())

    broken = [
        breach
        for breach in verdict.breaches
        if breach.contract == CONTRACT_SKILL_REFERENCE and breach.subject == "grill-me"
    ]
    assert broken, (
        f"expected the Skill reference grill-me to be named, got {verdict.breaches}"
    )
    assert "Skill reference does not resolve: grill-me" in str(broken[0])
    assert all(breach.contract != CONTRACT_REQUIRED_SKILL for breach in verdict.breaches)


def _front_door_skills(repo_root: Path) -> set[str]:
    """Skill names the front door lists, read independently of the verifier."""
    import re

    text = (repo_root / "README.md").read_text(encoding="utf-8")
    return set(re.findall(r"\|\s*\[`/([a-z0-9-]+)`\]", text))


def _write_triage_template(skills: Path, *, omit: str | None = None) -> None:
    from git_loopy.labels import TRIAGE_ROLES

    rows = [
        f"| `{spec.role}` | `{spec.name}` | meaning |"
        for spec in TRIAGE_ROLES
        if spec.name != omit
    ]
    skill = skills / "setup-git-loopy-skills"
    skill.mkdir(parents=True, exist_ok=True)
    if not (skill / "SKILL.md").is_file():
        _write_skill(skills, "setup-git-loopy-skills")
    (skill / "triage-labels.md").write_text(
        "# Triage Labels\n\n"
        "| Label in git-loopy/skills | Label in our tracker | Meaning |\n"
        "| --- | --- | --- |\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def test_a_dropped_provisioned_label_names_that_label(tmp_path: Path) -> None:
    """Omitting a default-named row must not hide behind the canonical fallback."""
    repo = _repo_root()
    checkout = tmp_path / "candidate"
    skills = checkout / "skills"
    for name in set(packaged_required_skills()) | _front_door_skills(repo):
        _write_skill(skills, name)
    _write_triage_template(skills, omit="ready-for-agent")

    verdict = verify_candidate(checkout, repo_root=repo)

    dropped = [
        breach
        for breach in verdict.breaches
        if breach.contract == CONTRACT_LABEL_VOCABULARY
        and breach.subject == "ready-for-agent"
    ]
    assert dropped, (
        "expected the Label vocabulary to name ready-for-agent, "
        f"got {verdict.breaches}"
    )
    assert "Label vocabulary stopped providing: ready-for-agent" in str(dropped[0])
    assert all(
        breach.contract == CONTRACT_LABEL_VOCABULARY for breach in verdict.breaches
    )


def _satisfying_checkout(tmp_path: Path, repo: Path) -> Path:
    checkout = tmp_path / "candidate"
    skills = checkout / "skills"
    for name in set(packaged_required_skills()) | _front_door_skills(repo):
        _write_skill(skills, name)
    _write_triage_template(skills)
    return checkout


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_a_satisfying_candidate_passes_without_modifying_the_pin(
    tmp_path: Path,
) -> None:
    repo = _repo_root()
    checkout = _satisfying_checkout(tmp_path, repo)
    pin_before = PIN_PATH.read_bytes()
    status_before = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout

    verdict = verify_candidate(checkout, repo_root=repo)

    assert verdict.passed is True
    assert verdict.breaches == ()
    assert PIN_PATH.read_bytes() == pin_before
    status_after = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    assert status_after == status_before


def test_a_working_clone_is_judged_as_it_stands_including_uncommitted_edits(
    tmp_path: Path,
) -> None:
    """No commit, push, or publish is required. HEAD is not the subject."""
    repo = _repo_root()
    checkout = _satisfying_checkout(tmp_path, repo)
    tdd = checkout / "skills" / "tdd"
    # Commit a catalog that lacks a Required Skill, then put that Skill on disk
    # without committing. A check that read HEAD would still report it absent.
    tdd_skill = (tdd / "SKILL.md").read_text(encoding="utf-8")
    for child in tdd.iterdir():
        child.unlink()
    tdd.rmdir()
    _git(checkout, "init", "--quiet", "-b", "main")
    _git(checkout, "config", "user.name", "Candidate Test")
    _git(checkout, "config", "user.email", "candidate-test@example.invalid")
    _git(checkout, "config", "commit.gpgsign", "false")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "--quiet", "-m", "catalog without tdd")
    tdd.mkdir()
    (tdd / "SKILL.md").write_text(tdd_skill, encoding="utf-8")
    assert _git(checkout, "status", "--porcelain") != ""

    verdict = verify_candidate(checkout, repo_root=repo)

    assert verdict.passed is True
    assert verdict.breaches == ()


def test_the_check_reaches_no_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"candidate check must not spawn a process: {args}")

    monkeypatch.setattr(subprocess, "run", _refuse)
    repo = _repo_root()
    checkout = _satisfying_checkout(tmp_path, repo)

    verdict = verify_candidate(checkout, repo_root=repo)

    assert verdict.passed is True


def test_the_operator_command_finds_the_repo_it_is_run_from(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _repo_root()
    checkout = _satisfying_checkout(tmp_path, repo)
    monkeypatch.chdir(repo)

    code = main([str(checkout)])

    captured = capsys.readouterr()
    assert code == 0
    assert "not a promise about the pin" in captured.out


def test_an_operator_can_judge_a_named_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo_root()
    checkout = _satisfying_checkout(tmp_path, repo)

    code = main([str(checkout), "--repo-root", str(repo)])

    captured = capsys.readouterr()
    assert code == 0
    assert "not a promise about the pin" in captured.out
    assert captured.err == ""


def test_the_operator_command_names_a_broken_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = tmp_path / "candidate"
    _write_skill(checkout / "skills", "code-review")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(
        "| [`/code-review`](https://github.com/bradcstevens/git-loopy-skills/tree/main/skills/code-review) | review |\n",
        encoding="utf-8",
    )

    code = main([str(checkout), "--repo-root", str(repo)])

    captured = capsys.readouterr()
    assert code == 1
    assert "Required Skill absent: tdd" in captured.err
    assert captured.out == ""


def test_the_operator_command_names_a_missing_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"

    code = main([str(missing), "--repo-root", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 1
    assert "does not exist" in captured.err
    assert "network" in captured.err


def test_the_operator_command_is_documented_beside_the_pin() -> None:
    guidance = (_repo_root() / "docs" / "skill-catalog-source.md").read_text(
        encoding="utf-8"
    )

    assert VERIFY_COMMAND in guidance
    assert "not a promise about the pin" in guidance
    assert "uncommitted" in guidance
