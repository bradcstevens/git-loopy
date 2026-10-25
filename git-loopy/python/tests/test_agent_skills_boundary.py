"""Keep setup-owned documentation separate from hand-owned workflow (#964)."""

from pathlib import Path
import re
import subprocess


def test_tracked_agent_skills_docs_keep_the_setup_boundary() -> None:
    """A setup refresh may replace only the template block and marked docs."""
    repo_root = Path(__file__).resolve().parents[3]
    templates = {
        "docs/agents/issue-tracker.md": "issue-tracker-github.md",
        "docs/agents/triage-labels.md": "triage-labels.md",
        "docs/agents/domain.md": "domain.md",
    }
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "AGENTS.md", *templates],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    assert {"AGENTS.md", *templates} <= set(tracked)

    agents = (repo_root / "AGENTS.md").read_text(encoding="utf-8")
    blocks = re.findall(r"^## Agent skills\n(.*?)(?=^## |\Z)", agents, re.M | re.S)
    # The boundary owns the headings and one summary/pointer per subsection,
    # not the template's prose: refreshing a reworded summary is harmless.
    expected = r"\n\n".join(
        rf"### {heading}\n\n[^\n]+ See `{re.escape(path)}`\."
        for heading, path in (
            ("Issue tracker", "docs/agents/issue-tracker.md"),
            ("Triage labels", "docs/agents/triage-labels.md"),
            ("Domain docs", "docs/agents/domain.md"),
        )
    )
    failures = []
    if len(blocks) != 1 or re.fullmatch(expected, blocks[0].strip()) is None:
        failures.append(
            "AGENTS.md: Agent skills must contain only the setup template's "
            "three summaries; keep hand-written guidance in Repository workflow."
        )
    if not re.search(
        r"^## Agent skills\n(?:(?!^## ).)*^## Repository workflow\n",
        agents,
        re.M | re.S,
    ):
        failures.append("AGENTS.md: Repository workflow must follow Agent skills.")

    for rel, template in templates.items():
        marker = (
            "<!-- Generated from `bradcstevens/git-loopy-skills/skills/"
            f"setup-git-loopy-skills/{template}`; refreshing the skills setup "
            "rewrites this file. -->"
        )
        text = (repo_root / rel).read_text(encoding="utf-8")
        if not text.startswith(marker + "\n"):
            failures.append(f"{rel}: missing opening template line: {marker}")

    assert not failures, "\n".join(failures)
