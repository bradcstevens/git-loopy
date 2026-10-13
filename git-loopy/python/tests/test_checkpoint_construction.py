"""ADR-0090: discover commit sites, rather than freeze today's Checkpoint list.

The supported construction is deliberately simple: pass an unmodified call to
the imported ``wrapper.checkpoint_message`` directly to ``commit``. Indirection
must not silently weaken the guard. Raw command vectors (including ones assigned
before execution) and shell commands are reviewed too, not just GitClient calls.
This is a source regression guard, not a runtime or adversarial Python verifier.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest


PACKAGE = Path(__file__).resolve().parents[1] / "git_loopy"
Site = tuple[str, str, str]

# Exceptions name one exact expression in one scope, never a whole function or
# file. The suite rejects stale entries and duplicate uses of an exception.
EXCEPTIONS: dict[Site, str] = {
    (
        "git.py", "SubprocessGitClient.commit",
        "['commit', '-m', message]",
    ): "Transport only: forwards the caller's message; callers are scanned separately.",
    (
        "git.py", "SubprocessGitClient.commit_paths",
        "['commit', '-m', message, '--', *pathspecs]",
    ): "Path-scoped transport only; every commit_paths caller is scanned separately.",
    (
        "demotion.py", "demote_after_run",
        "git.commit_paths(_commit_message(plan), [path])",
    ): (
        "ADR-0028/#366: persist an explicit Measured-routing Demotion, not unfinished "
        "Agent work. Its own reviewed artifact commit is not a Lane/host Checkpoint."
    ),
    (
        "loop.py", "_ParallelLoop._advance_release_line",
        "self._git.commit_paths(release_line_commit_subject(next_line.version), "
        "(*RELEASE_VERSION_PATHS, *notes_write.commit_paths))",
    ): (
        "ADR-0066: publish Release-line metadata after closing an integrated issue; "
        "this release operation is not a dirty-tree Checkpoint or salvage."
    ),
    (
        "release_rehearsal.py", "_promote_milestone",
        "_run_git(workspace, 'commit', '-aqm', outputs['subject'])",
    ): (
        "ADR-0059: rehearse the requested milestone Promotion in a disposable clone "
        "using the Release writer's subject, not capture unfinished Agent work."
    ),
    (
        "release_smoke.py", "GitHubSandbox.publish_base",
        "['git', 'commit', '-qm', f'git-loopy release smoke base {smoke_id}']",
    ): (
        "Seed the explicitly requested disposable smoke repository before a Run; "
        "this is a test fixture's initial history, not a contribution Checkpoint."
    ),
}


def _command_vector(values: list[ast.expr]) -> bool:
    """Recognize git command tokens without mistaking payload field names for CLI."""
    for index, value in enumerate(values):
        if not isinstance(value, ast.Constant) or value.value != "commit":
            continue
        before = values[:index]
        after = values[index + 1:]
        return (
            index == 0
            or any(isinstance(v, ast.Constant) and v.value == "git" for v in before)
            or any(
                isinstance(v, ast.Constant)
                and isinstance(v.value, str)
                and v.value.startswith("-")
                for v in after
            )
        )
    return False


def _sites(
    tree: ast.AST, scope: str = "", parent: ast.AST | None = None
) -> Iterator[tuple[str, ast.expr]]:
    if isinstance(tree, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        scope = f"{scope}.{tree.name}".strip(".")
    if isinstance(tree, ast.Call):
        function = tree.func
        name = (
            function.attr if isinstance(function, ast.Attribute)
            else function.id if isinstance(function, ast.Name) else ""
        )
        if name in {"commit", "commit_paths"}:
            yield scope, tree
        # A command passed as positional arguments, e.g. _run_git(root, "commit",
        # "-m", message). A single get("commit") is just a payload field lookup.
        elif len(tree.args) > 1 and _command_vector(tree.args):
            yield scope, tree
    elif isinstance(tree, (ast.List, ast.Tuple)) and _command_vector(tree.elts):
        yield scope, tree
    elif (
        isinstance(tree, ast.Attribute)
        and tree.attr in {"commit", "commit_paths"}
        and isinstance(parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        and parent.value is tree
    ):
        # An alias must not hide a later call from this source-level guard.
        yield scope, tree
    for child in ast.iter_child_nodes(tree):
        # Exclude docstrings and do not count a shell f-string's literal chunks
        # separately from its complete command.
        if isinstance(child, (ast.Constant, ast.JoinedStr)) and not isinstance(
            tree, (ast.Expr, ast.JoinedStr)
        ):
            text = child.value if isinstance(child, ast.Constant) else ast.unparse(child)
            if isinstance(text, str) and re.search(
                r"\bgit\s+(?:-\S+(?:\s+(?!commit\b)\S+)?\s+)*commit\b", text
            ):
                yield scope, child
        yield from _sites(child, scope, tree)


def _assert_checkpoint_construction(
    package: Path, exceptions: dict[Site, str]
) -> None:
    violations = []
    used: Counter[Site] = Counter()
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        helpers = {
            alias.asname or alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "git_loopy.wrapper"
            for alias in node.names
            if alias.name == "checkpoint_message"
        }
        for scope, node in _sites(tree):
            site = (path.relative_to(package).as_posix(), scope, ast.unparse(node))
            if site in exceptions:
                assert exceptions[site].strip(), f"Unreasoned exception: {site}"
                used[site] += 1
                continue
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, (ast.Attribute, ast.Name))
                and (
                    node.func.attr if isinstance(node.func, ast.Attribute)
                    else node.func.id
                ) in {"commit", "commit_paths"}
            ):
                message = node.args[0] if node.args else next(
                    (kw.value for kw in node.keywords if kw.arg == "message"), None
                )
                if (
                    isinstance(message, ast.Call)
                    and isinstance(message.func, ast.Name)
                    and message.func.id in helpers
                ):
                    continue
            violations.append(f"{site[0]}:{node.lineno} ({scope}): {site[2]}")
    assert not violations, "Commits must use checkpoint_message:\n" + "\n".join(violations)
    assert used == Counter({site: 1 for site in exceptions}), (
        f"Stale or duplicated commit exceptions: {used}"
    )


def test_self_initiated_commits_use_checkpoint_message() -> None:
    _assert_checkpoint_construction(PACKAGE, EXCEPTIONS)


@pytest.mark.parametrize(
    "statement",
    [
        'git.commit("Closes #783")',
        'git.commit(message="not a Checkpoint")',
        'git.commit_paths("not a Checkpoint", paths)',
        'git.commit(checkpoint_message(783) + "\\nCloses #783")',
        'subprocess.run(["git", "commit", "-m", "not a Checkpoint"])',
        'argv = ["git", "-C", root, "commit", "-m", "not a Checkpoint"]',
        '_run_git(root, "commit", "-m", "not a Checkpoint")',
        'subprocess.run("git commit -m wrong", shell=True)',
        'subprocess.run(f"git commit -m {message}", shell=True)',
        'subprocess.run("git -C workspace commit -m wrong", shell=True)',
        'save = git.commit; save("not a Checkpoint")',
    ],
)
def test_guard_rejects_a_new_nonconforming_site(tmp_path: Path, statement: str) -> None:
    package = tmp_path / "git_loopy"
    (package / "new_host").mkdir(parents=True)
    (package / "new_host" / "worker.py").write_text(
        "from git_loopy.wrapper import checkpoint_message\n"
        f"def clean_tree():\n    {statement}\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="worker.py:3.*clean_tree"):
        _assert_checkpoint_construction(package, {})


@pytest.mark.parametrize(
    "statement",
    [
        "git.commit(checkpoint_message(783))",
        "git.commit(message=checkpoint_message(None))",
    ],
)
def test_guard_accepts_a_new_checkpoint_site(tmp_path: Path, statement: str) -> None:
    (tmp_path / "new_host.py").write_text(
        "from git_loopy.wrapper import checkpoint_message\n"
        f"def clean_tree():\n    {statement}\n",
        encoding="utf-8",
    )
    _assert_checkpoint_construction(tmp_path, {})


@pytest.mark.parametrize(
    ("module", "scope"),
    [
        ("loop.py", "_Loop._maybe_checkpoint"),
        ("loop.py", "_ParallelLoop._salvage_and_reclaim_lane_workspace"),
        ("loop.py", "_ParallelLoop._maybe_checkpoint_lane"),
        ("sweep.py", "_reclaim_dead_workspaces"),
        ("github_actions_worker.py", "checkpoint_dirty_tree"),
    ],
)
def test_guard_rejects_bypassing_the_helper_at_each_existing_checkpoint(
    tmp_path: Path, module: str, scope: str
) -> None:
    """Mutate copies only: production files and shared worktrees stay untouched."""
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    calls = [
        node for owner, node in _sites(tree)
        if owner == scope and isinstance(node, ast.Call)
    ]
    assert len(calls) == 1, f"Expected one Checkpoint to mutate in {module}:{scope}"
    calls[0].args[0] = ast.Constant(value="Closes #783")
    (tmp_path / module).write_text(ast.unparse(tree), encoding="utf-8")
    exceptions = {site: reason for site, reason in EXCEPTIONS.items() if site[0] == module}
    with pytest.raises(AssertionError, match=re.escape(scope)):
        _assert_checkpoint_construction(tmp_path, exceptions)


def test_exception_does_not_allow_another_commit_in_the_same_scope(tmp_path: Path) -> None:
    (tmp_path / "transport.py").write_text(
        'def commit(message):\n'
        '    _run(["commit", "-m", message])\n'
        '    _run(["commit", "-m", "not a Checkpoint"])\n',
        encoding="utf-8",
    )
    exceptions = {
        ("transport.py", "commit", "['commit', '-m', message]"): "Caller-owned transport."
    }
    with pytest.raises(AssertionError, match="transport.py:3"):
        _assert_checkpoint_construction(tmp_path, exceptions)


@pytest.mark.parametrize("copies", [0, 2])
def test_exceptions_cannot_go_stale_or_cover_duplicate_sites(
    tmp_path: Path, copies: int
) -> None:
    (tmp_path / "transport.py").write_text(
        "def commit(message):\n    pass\n"
        + '    _run(["commit", "-m", message])\n' * copies,
        encoding="utf-8",
    )
    exceptions = {
        ("transport.py", "commit", "['commit', '-m', message]"): "Caller-owned transport."
    }
    with pytest.raises(AssertionError, match="Stale or duplicated"):
        _assert_checkpoint_construction(tmp_path, exceptions)
