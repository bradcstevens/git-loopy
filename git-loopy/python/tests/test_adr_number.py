"""The next ADR number and its remote evidence are reported offline (#965)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from git_loopy.adr_number import main
from git_loopy.gate import parse_feedback_loops
from git_loopy.gh import GhError
from git_loopy.git import GitError


def test_the_command_reports_the_next_number_and_remote_evidence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(
        [],
        read_main=lambda: (
            "docs/adr/0001-first.md",
            "docs/adr/0057-existing.md",
            "docs/adr/0105-highest.md",
            "docs/adr/README.md",
        ),
        read_pull_requests=lambda: {
            42: ("docs/adr/0106-pending.md", "docs/adr/0109-also-pending.md"),
            7: ("README.md", "docs/adr/0107-another.md"),
            99: ("git-loopy/python/README.md",),
        },
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "Next free ADR number: 0110\n"
        "Highest on origin/main: 0105\n"
        "Open pull requests adding ADR files:\n"
        "  #7:\n"
        "    docs/adr/0107-another.md\n"
        "  #42:\n"
        "    docs/adr/0106-pending.md\n"
        "    docs/adr/0109-also-pending.md\n"
    )


@pytest.mark.parametrize(
    ("stage", "error"),
    [
        ("main", GitError(["git", "fetch", "origin"], 1, "fetch refused")),
        ("main", GitError(["git", "ls-tree", "origin/main"], 1, "tree unreadable")),
        ("pull_requests", GhError(["gh", "api", "pulls"], 1, "PR listing failed")),
        ("pull_requests", GhError(["gh", "api", "files"], 1, "file listing failed")),
        ("main", OSError("git could not execute")),
        ("pull_requests", OSError("gh could not execute")),
    ],
)
def test_a_read_failure_reports_an_error_and_prints_no_number(
    stage: str, error: Exception, capsys: pytest.CaptureFixture[str],
) -> None:
    def failed_read():
        raise error

    rc = main(
        [],
        read_main=failed_read if stage == "main" else (
            lambda: ("docs/adr/0105-highest.md",)
        ),
        read_pull_requests=failed_read if stage == "pull_requests" else (
            lambda: {42: ("docs/adr/0106-pending.md",)}
        ),
    )

    captured = capsys.readouterr()
    assert rc != 0
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert str(error) in captured.err


def test_the_default_command_fetches_main_and_reads_every_pr_file_page_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    origin, clone = tmp_path / "origin", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
    adr = origin / "docs/adr"
    adr.mkdir(parents=True)
    (adr / "0105-existing.md").write_text("# Existing\n", encoding="utf-8")

    def commit() -> None:
        subprocess.run(["git", "-C", str(origin), "add", "."], check=True)
        subprocess.run(
            [
                "git", "-C", str(origin), "-c", "user.name=Offline",
                "-c", "user.email=offline@example.test",
                "commit", "--no-gpg-sign", "-qm", "ADR evidence",
            ],
            check=True,
        )

    commit()
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    (adr / "0108-fresh.md").write_text("# Fresh\n", encoding="utf-8")
    commit()
    (clone / "docs/adr/0999-local-draft.md").write_text(
        "# Local draft is not remote evidence\n", encoding="utf-8",
    )
    pages = {
        "repos/{owner}/{repo}/pulls?state=open&per_page=100": [
            [{"number": 42, "draft": True, "base": {"ref": "another-branch"}}],
            [{"number": 7}],
        ],
        "repos/{owner}/{repo}/pulls/42/files?per_page=100": [
            [
                {"filename": "docs/adr/0109-pending.md", "status": "added"},
                {"filename": "docs/adr/0997-modified.md", "status": "modified"},
            ],
            [
                {"filename": "docs/adr/0110-pending.md", "status": "added"},
                {"filename": "docs/adr/0998-removed.md", "status": "removed"},
                {"filename": "docs/adr/0999-renamed.md", "status": "renamed"},
                {"filename": "docs/adr/README.md", "status": "added"},
            ],
        ],
        "repos/{owner}/{repo}/pulls/7/files?per_page=100": [
            [{"filename": "README.md", "status": "added"}],
        ],
    }
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[0] != "gh":
            return real_run(args, **kwargs)
        assert args[1] == "api"
        assert args[3:] == ["--paginate", "--slurp"]
        return subprocess.CompletedProcess(args, 0, json.dumps(pages[args[2]]), "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.chdir(clone)

    rc = main([])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert captured.out == (
        "Next free ADR number: 0111\n"
        "Highest on origin/main: 0108\n"
        "Open pull requests adding ADR files:\n"
        "  #42:\n"
        "    docs/adr/0109-pending.md\n"
        "    docs/adr/0110-pending.md\n"
    )


@pytest.mark.parametrize("stage", ["fetch", "ls-tree", "pulls", "files"])
def test_default_command_read_failures_never_print_partial_evidence(
    stage: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def run(args, **kwargs):
        operation = args[1] if args[0] == "git" else (
            "files" if "/files?" in args[2] else "pulls"
        )
        if operation == stage:
            return subprocess.CompletedProcess(args, 1, "", "read refused")
        stdout = {
            "fetch": "",
            "ls-tree": "docs/adr/0105-highest.md\0",
            "pulls": '[[{"number": 42}]]',
            "files": '[[]]',
        }[operation]
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", run)

    rc = main([])

    captured = capsys.readouterr()
    assert rc != 0
    assert captured.out == ""
    assert "read refused" in captured.err


@pytest.mark.parametrize(
    ("main_paths", "pull_requests", "expected"),
    [
        ((), {}, "0001"),
        (("docs/adr/README.md",), {42: ("README.md",)}, "0001"),
        (
            ("docs/adr/0057-first.md", "docs/adr/0057-second.md"),
            {42: ("docs/adr/0050-old.md",)},
            "0058",
        ),
        (
            ("docs/adr/0105-highest.md", "docs/adr/nested/0999-ignored.md"),
            {42: ("other/docs/adr/0999-ignored.md",)},
            "0106",
        ),
    ],
)
def test_empty_or_older_pending_listings_do_not_invent_higher_numbers(
    main_paths: tuple[str, ...], pull_requests: dict[int, tuple[str, ...]],
    expected: str, capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(
        [], read_main=lambda: main_paths,
        read_pull_requests=lambda: pull_requests,
    ) == 0
    assert capsys.readouterr().out.startswith(f"Next free ADR number: {expected}\n")


def test_a_pull_request_at_the_api_file_limit_is_not_a_complete_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def run(args, **kwargs):
        if args[0] == "git":
            stdout = "" if args[1] == "fetch" else "docs/adr/0105-existing.md\0"
        elif "/files?" in args[2]:
            stdout = json.dumps([[
                {"filename": f"other/{number}.txt", "status": "added"}
                for number in range(3000)
            ]])
        else:
            stdout = '[[{"number": 42}]]'
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", run)

    rc = main([])

    captured = capsys.readouterr()
    assert rc != 0
    assert captured.out == ""
    assert "3000" in captured.err
    assert "#42" in captured.err
    assert "incomplete" in captured.err


def test_the_command_is_disclosed_to_adr_authors_but_excluded_from_the_gate() -> None:
    root = Path(__file__).resolve().parents[3]
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    commands = [loop.command for loop in parse_feedback_loops(agents)]
    assert commands
    assert all("adr_number" not in command for command in commands)
    reference = (root / "docs/agents/feedback-loops.md").read_text(encoding="utf-8")
    assert "python -m git_loopy.adr_number" in reference
    exclusion = reference[reference.index("python -m git_loopy.adr_number"):]
    exclusion = exclusion.split("\n- **", 1)[0]
    assert "unreachable" in exclusion
    assert "tests/test_adr_number.py" in exclusion
    workflow = agents.split("## Repository workflow\n", 1)[1]
    assert "python -m git_loopy.adr_number" in workflow
    assert "writing an ADR" in workflow


@pytest.mark.parametrize(
    ("listing", "files"),
    [
        ("not JSON", "[[]]"),
        ('{"error": "unavailable"}', "[[]]"),
        ("[]", "[[]]"),
        ('[[{"number": true}]]', "[[]]"),
        ('[[{"number": 42}]]', "not JSON"),
        ('[[{"number": 42}]]', "[[null]]"),
        ('[[{"number": 42}]]', '[[{"filename": "", "status": "added"}]]'),
        (
            '[[{"number": 42}]]',
            '[[{"filename": "docs/adr/0106-pending.md", "status": "unknown"}]]',
        ),
    ],
)
def test_malformed_remote_listings_are_errors_not_free_numbers(
    listing: str, files: str,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def run(args, **kwargs):
        if args[0] == "git":
            stdout = "" if args[1] == "fetch" else "docs/adr/0105-existing.md\0"
        else:
            stdout = files if "/files?" in args[2] else listing
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", run)

    rc = main([])

    captured = capsys.readouterr()
    assert rc != 0
    assert captured.out == ""
    assert captured.err.startswith("error: ")
