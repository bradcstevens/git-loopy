"""Integrated documentation acceptance for #958; behavioral proofs stay in their suites.

The agreed seams are tracked repository instructions and the gate's public
table parser. Story 18 follows #1039's later move of policy and flakes into
the disclosed reference, rather than restoring the superseded inline copy.
"""

from pathlib import Path
import re
import subprocess

import pytest

from git_loopy.gate import parse_feedback_loops


@pytest.fixture(scope="module")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def agents(repo_root: Path) -> str:
    return (repo_root / "AGENTS.md").read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    match = re.search(
        rf"^{re.escape(heading)}\n(.*?)(?=^#{{1,{len(heading.split()[0])}}} |\Z)",
        text,
        re.M | re.S,
    )
    assert match is not None, f"missing section: {heading}"
    return match[1]


def test_repository_workflow_preserves_the_moved_guidance_in_order(
    agents: str,
    repo_root: Path,
) -> None:
    workflow = section(agents, "## Repository workflow")
    guidance = (
        ("Agent skills come from", "source of record", "ADR-0025"),
        (
            "seven **Agent profiles**",
            "Required and Permitted Skills",
            "docs/skill-policy.md",
        ),
        ("For human-led planning", "git-loopy plan", "planning-git-loopy-plan"),
        ("### Chain hook", "docs/agents/chain-hooks.md"),
        (
            "### Planning documents and Task types",
            "never",
            "ready-for-agent",
            "Only a human asserts",
            "non_execution_task_type",
        ),
        (
            "### Release milestones",
            "**Promotion**",
            "Release-target",
            "docs/releases/README.md",
        ),
        (
            "### Glossary former names",
            "renamed in #790",
            "GLOSSARY.md",
            "GLOSSARY-MAP.md",
            "never recreate",
        ),
    )
    positions = [workflow.index(fragments[0]) for fragments in guidance]
    assert positions == sorted(positions)
    for index, fragments in enumerate(guidance):
        end = positions[index + 1] if index + 1 < len(positions) else len(workflow)
        moved = " ".join(workflow[positions[index] : end].split())
        for fragment in fragments:
            assert fragment.lower() in moved.lower(), fragment
    for target in (
        "docs/skill-policy.md",
        "git-loopy/python/README.md",
        "docs/agents/chain-hooks.md",
        "docs/releases/README.md",
    ):
        assert (repo_root / target).is_file(), target


@pytest.mark.parametrize(
    ("heading", "trigger", "target"),
    [
        (
            "## Feedback loops",
            "For maintainer rationale on whether a command should become a gate row",
            "docs/agents/feedback-loops.md",
        ),
        (
            "## Repository workflow",
            "When changing a Skill or Agent profile",
            "docs/agents/skills-repository.md",
        ),
    ],
)
def test_disclosed_references_are_tracked_and_reachable_at_the_point_of_need(
    agents: str,
    repo_root: Path,
    heading: str,
    trigger: str,
    target: str,
) -> None:
    body = section(agents, heading)
    assert any(trigger in line and f"]({target})" in line for line in body.splitlines())
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", target],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    assert target in tracked
    assert (repo_root / target).read_text(encoding="utf-8").startswith("# ")


def test_disclosure_keeps_maintainer_rationale_out_of_agent_instructions(
    agents: str,
    repo_root: Path,
) -> None:
    exclusions = (repo_root / "docs/agents/feedback-loops.md").read_text(
        encoding="utf-8"
    )
    skills = (repo_root / "docs/agents/skills-repository.md").read_text(
        encoding="utf-8"
    )
    assert "## Feedback-loop exclusions" in exclusions
    assert "deliberately *not* in the table" in exclusions
    assert "### Feedback-loop exclusions" not in agents
    assert "pre-existing errors" not in section(agents, "## Feedback loops")
    assert "git clone" not in agents
    assert "git clone" in skills
    assert "pin is last" in skills


def test_disclosure_preserves_the_feedback_loop_table_byte_for_byte(
    agents: str,
) -> None:
    expected = (
        "| Loop | Command | When to run |\n"
        "| --- | --- | --- |\n"
        "| Shell syntax | `bash -n git-loopy/shell/git-loopy.sh git-loopy/shell/install.sh git-loopy/shell/lib/*.sh git-loopy/shell/tests/*.sh` | Any change under `git-loopy/shell/` |\n"
        "| Python suite | `uv run --project git-loopy/python --all-extras python -m pytest -q git-loopy/python/tests` | Any change to the Python Runner, the Conformance fixtures, `PROMPT.md`, the Skill catalog, or the CI workflows |\n"
        "| Rust Dashboard core | `cargo fmt --manifest-path git-loopy/tui/Cargo.toml --check && cargo clippy --manifest-path git-loopy/tui/Cargo.toml --all-targets -- -D warnings && cargo test --manifest-path git-loopy/tui/Cargo.toml` | Any change under `git-loopy/tui/` or to the Event schema the Dashboard reads |\n"
        r"| Shell Orchestrator suites | `for t in git-loopy/shell/tests/test-*.sh; do bash "
        r'"$t" \|\| exit 1; done` | Any change under `git-loopy/shell/` or to a Conformance fixture |'
        "\n"
        r"| PowerShell Orchestrator suites | `for t in git-loopy/powershell/tests/test-*.ps1; do pwsh -NoLogo -NoProfile -File "
        r'"$t" \|\| exit 1; done` | Any change under `git-loopy/powershell/` or to a Conformance fixture |'
        "\n"
    )
    body = section(agents, "## Feedback loops")
    table = re.search(r"^\| Loop \|.*?(?=\n\n|\Z)", body, re.M | re.S)
    assert table is not None
    assert table[0] + "\n" == expected
    assert len(parse_feedback_loops(agents)) == 5


@pytest.mark.parametrize(
    ("note", "rationale", "excluded"),
    [
        ("`ruff`", ("pre-existing errors",), ("ruff",)),
        (
            "The Conformance adapter subset",
            ("pay for it twice",),
            ("test_conformance.py", "test_release_identity_conformance.py"),
        ),
        (
            "The native Windows timezone proof",
            ("Windows APIs", "CI supplies"),
            ("runner-family-gate.yml",),
        ),
        (
            "`python -m git_loopy.skill_source`",
            ("unreachable upstream", "test_skill_source.py"),
            ("skill_source",),
        ),
        (
            "`git-loopy doctor --apply`",
            ("refreshes", "test_skill_install.py", "test_doctorcmd.py"),
            ("doctor",),
        ),
        (
            "`python -m git_loopy.sdk_feed`",
            ("unreachable feed", "test_sdk_feed.py"),
            ("sdk_feed",),
        ),
        (
            "`python -m git_loopy.adr_number`",
            ("unreachable remote", "test_adr_number.py"),
            ("adr_number",),
        ),
        (
            "A paired Skill change",
            ("working surface, not a loop",),
            ("skill_candidate", "git-loopy-skills"),
        ),
        (
            "The cross-repo Skills proof",
            ("unreachable", "test_cross_repo_skills_proof_workflow.py"),
            ("cross-repo-skills-proof",),
        ),
        (
            "`python -m git_loopy.release_rehearsal`",
            ("recursively", "test_release_rehearsal.py"),
            ("release_rehearsal",),
        ),
    ],
)
def test_every_disclosed_exclusion_remains_enforced(
    agents: str,
    repo_root: Path,
    note: str,
    rationale: tuple[str, ...],
    excluded: tuple[str, ...],
) -> None:
    reference = (repo_root / "docs/agents/feedback-loops.md").read_text(
        encoding="utf-8"
    )
    notes = section(reference, "## Feedback-loop exclusions").split("\n- **")
    matching = [body for body in notes if body.startswith(note + "**")]
    assert len(matching) == 1, f"missing or duplicated exclusion: {note}"
    body = " ".join(matching[0].split())
    for fragment in rationale:
        assert fragment in body, (note, fragment)
    commands = [loop.command for loop in parse_feedback_loops(agents)]
    assert commands
    assert not [
        command for command in commands if any(token in command for token in excluded)
    ], f"{note} must not become a blocking gate row"


def test_targeted_rule_and_all_known_flakes_remain_disclosed_after_1039(
    agents: str,
    repo_root: Path,
) -> None:
    feedback = section(agents, "## Feedback loops")
    assert "targeted_tests.py" in feedback
    assert "docs/agents/feedback-loops.md#targeted-python-tests" in feedback
    rules = section(
        (repo_root / "docs/agents/feedback-loops.md").read_text(encoding="utf-8"),
        "## Targeted Python tests",
    )
    for fragment in (
        "ast.dump",
        "conftest.py",
        "shared fixture or fake module",
        "production module",
        "every new and edited test module, in full",
        "test_release_installation.py::test_reclaims_setup_children_not_just_their_parent[cancelled-init]",
        "test_release_smoke.py::test_a_run_still_in_flight_at_the_deadline_is_never_passed",
        "test_attach_command.py::test_a_dashboard_fault_restores_the_terminal",
        "rerun it alone",
        "also fails alone is a real failure",
    ):
        assert fragment in " ".join(rules.split()), fragment
