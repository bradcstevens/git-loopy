"""Contributor selector contract, using private repositories without acquisition."""

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest


COMMAND = Path(__file__).resolve().parents[1] / "targeted_tests.py"
TESTS = "git-loopy/python/tests/"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Selector Test")
    git(tmp_path, "config", "user.email", "selector@example.invalid")
    for path, content in {
        "README.md": "baseline\n",
        "module.py": '"""old"""\nVALUE = 1\n',
        TESTS + "test_module.py": "def test_module(): assert True\n",
        TESTS + "test_no_retired.py": "def test_guard(): assert True\n",
        TESTS + "test_glossary_terms.py": "def test_terms(): assert True\n",
        TESTS + "baseline.json": "{}\n",
        TESTS + "conftest.py": "VALUE = 1\n",
        TESTS + "fake.py": "VALUE = 1\n",
        ".gitignore": "ignored*\n",
    }.items():
        write(tmp_path, path, content)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "baseline")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(
        tmp_path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"
    )
    return tmp_path


def command(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Inherit HOME, XDG and package-feed settings unchanged. Never install anything.
    return subprocess.run(
        [sys.executable, str(COMMAND), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def report(repo: Path, *args: str) -> tuple[int, dict]:
    result = command(repo, "--json", *args)
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize("backstop", ["ci", "integration"])
def test_markdown_and_evidence(repo: Path, backstop: str) -> None:
    write(repo, "README.md", "new\n")
    code, result = report(repo, "--backstop", backstop)
    assert code == 0
    assert result["verdict"] == "targeted"
    assert result["change_class"] == "Markdown-only"
    assert result["modules"] == [
        TESTS + "test_glossary_terms.py",
        TESTS + "test_no_retired.py",
    ]
    assert result["backstop"] == backstop
    assert f"full-suite-backstop={backstop}" in result["evidence"]
    assert json.dumps(result["modules"]) in result["evidence"]
    human = command(repo, "--backstop", backstop)
    assert human.returncode == 0
    assert result["evidence"] in human.stdout
    assert "Verdict: targeted" in human.stdout


@pytest.mark.parametrize(
    "source",
    [
        '"""new"""\nVALUE = 1\n',
        '# prose\n"""old"""\nVALUE = 1\n',
        '"""old"""\nVALUE=1 # prose\n',
    ],
)
def test_docstring_or_prose(repo: Path, source: str) -> None:
    write(repo, "module.py", source)
    code, result = report(repo, "--backstop", "ci")
    assert code == 0
    assert result["change_class"] == "docstring-or-prose"
    assert TESTS + "test_module.py" in result["modules"]


def test_class_function_async_docstrings(repo: Path) -> None:
    original = '''
"""module"""
class Thing:
    """class"""
    def method(self):
        """method"""
        return 1
async def run():
    """async"""
    return 1
'''
    write(repo, "nested.py", original)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "nested baseline")
    write(
        repo,
        "nested.py",
        original.replace('"""module"""', '"""new module"""')
        .replace('"""class"""', '"""new class"""')
        .replace('"""method"""', '"""new method"""')
        .replace('"""async"""', '"""new async"""'),
    )
    assert report(repo, "--base", "HEAD", "--backstop", "ci")[0] == 0
    write(repo, "nested.py", original.replace("return 1", "return 2"))
    assert report(repo, "--base", "HEAD", "--backstop", "ci")[0] == 10


@pytest.mark.parametrize(
    "path,content",
    [
        ("module.py", "VALUE = 2\n"),
        ("module.py", "not valid python !\n"),
        ("new.py", '"""only prose"""\n'),
        (TESTS + "conftest.py", "VALUE = 2\n"),
        (TESTS + "fake.py", "VALUE = 2\n"),
        (TESTS + "fixtures/shared.json", "{}"),
        (TESTS + "baseline.json", '{"new": true}'),
        ("git-loopy/python/pyproject.toml", "[project]\n"),
        ("git-loopy/python/uv.lock", "version = 1\n"),
        ("package-lock.json", "{}"),
        ("script.sh", "echo hello\n"),
    ],
)
def test_disqualifying_categories(repo: Path, path: str, content: str) -> None:
    write(repo, path, content)
    code, result = report(repo, "--backstop", "integration")
    assert code == 10
    assert result["verdict"] == "full"
    assert path in result["reason"]
    assert command(repo, "--backstop", "ci", "--run").returncode == 10


def test_test_changes_and_owned_additions(repo: Path) -> None:
    write(
        repo,
        TESTS + "test_module.py",
        'DATA = "new.json"\ndef test_ok(): assert True\n',
    )
    write(repo, TESTS + "new.json", "{}")
    write(repo, TESTS + "test_added.py", "def test_added(): assert True\n")
    write(repo, TESTS + "test_added/sample.txt", "owned\n")
    write(repo, "README.md", "changed\n")
    code, result = report(repo, "--backstop", "integration")
    assert code == 0
    assert result["change_class"] == "test-only"
    assert TESTS + "test_added.py" in result["modules"]
    assert all(path.endswith(".py") for path in result["modules"])


def test_shared_data_is_not_owned_by_changed_tests(repo: Path) -> None:
    write(repo, TESTS + "test_other.py", 'DATA = "shared.json"\n')
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "shared owner")
    write(repo, TESTS + "test_module.py", 'DATA = "shared.json"\n')
    write(repo, TESTS + "shared.json", "{}")
    assert report(repo, "--base", "HEAD", "--backstop", "ci")[0] == 10


@pytest.mark.parametrize(
    "path",
    [
        "fixtures/shared.json",
        "fakes/reply.json",
        "test_module/requirements.txt",
        "test_module/uv.lock",
        "test_module/package.json",
    ],
)
def test_shared_and_dependency_data_cannot_claim_ownership(
    repo: Path, path: str
) -> None:
    write(repo, TESTS + "test_module.py", f'DATA = "{Path(path).name}"\n')
    write(repo, TESTS + path, "{}")
    assert report(repo, "--backstop", "ci")[0] == 10


def test_inspection_never_fetches_or_changes_index(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = repo / "ignored-trace"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    write(repo, "README.md", "changed\n")
    index = repo / ".git/index"
    before = index.read_bytes()
    assert report(repo, "--backstop", "ci")[0] == 0
    assert index.read_bytes() == before
    assert "fetch" not in trace.read_text()


@pytest.mark.parametrize(
    "path", ["module.py", TESTS + "test_module.py", TESTS + "fake.py"]
)
@pytest.mark.parametrize("rename", [False, True])
def test_python_deletions_and_renames(repo: Path, path: str, rename: bool) -> None:
    if rename:
        git(repo, "mv", path, path.replace(".py", "_renamed.py"))
    else:
        (repo / path).unlink()
    code, result = report(repo, "--backstop", "ci")
    assert code == 10
    assert path in result["reason"]
    assert path not in result["modules"]


@pytest.mark.parametrize("layer", ["committed", "staged", "unstaged", "untracked"])
def test_all_change_layers(repo: Path, layer: str) -> None:
    path = TESTS + ("test_new.py" if layer == "untracked" else "test_module.py")
    write(repo, path, "def test_changed(): assert True\n")
    if layer in {"committed", "staged"}:
        git(repo, "add", ".")
    if layer == "committed":
        git(repo, "commit", "-qm", "change")
    before = git(repo, "status", "--porcelain=v1")
    code, result = report(repo, "--backstop", "ci")
    assert code == 0
    assert path in result["modules"]
    assert git(repo, "status", "--porcelain=v1") == before


def test_mixed_layers_and_ignored_files(repo: Path) -> None:
    write(repo, "README.md", "committed\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "committed")
    write(repo, TESTS + "test_module.py", "def test_staged(): assert True\n")
    git(repo, "add", ".")
    write(repo, TESTS + "test_module.py", "def test_unstaged(): assert True\n")
    write(repo, TESTS + "test_new.py", "def test_new(): assert True\n")
    write(repo, "ignored_bad.py", "invalid !")
    assert report(repo, "--backstop", "ci")[0] == 0
    write(repo, "bad.py", "VALUE = 2\n")
    assert report(repo, "--backstop", "ci")[0] == 10


def test_default_explicit_and_missing_bases(repo: Path) -> None:
    baseline = git(repo, "rev-parse", "HEAD")
    write(repo, "module.py", "VALUE = 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "production")
    write(repo, "README.md", "new\n")
    assert report(repo, "--backstop", "ci")[0] == 10
    code, result = report(repo, "--base", "HEAD", "--backstop", "ci")
    assert code == 0
    assert result["base"] != baseline
    git(repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    missing = command(repo, "--backstop", "ci")
    assert missing.returncode == 1
    assert not missing.stdout
    assert "--base" in missing.stderr
    assert report(repo, "--base", "HEAD", "--backstop", "ci")[0] == 0
    assert command(repo, "--base", "missing", "--backstop", "ci").returncode == 1


def test_no_backstop_and_no_changes(repo: Path) -> None:
    assert report(repo, "--backstop", "ci")[0] == 10
    write(repo, "README.md", "new\n")
    code, result = report(repo)
    assert code == 10
    assert "--backstop" in result["reason"]
    assert result["backstop"] is None


def test_default_head_need_not_be_main(repo: Path) -> None:
    write(repo, "module.py", "VALUE = 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "trunk baseline")
    git(repo, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    write(repo, "README.md", "new\n")
    code, result = report(repo, "--backstop", "ci")
    assert code == 0
    assert result["base"] == git(repo, "rev-parse", "HEAD")


def test_default_uses_merge_base_not_remote_tip(repo: Path) -> None:
    baseline = git(repo, "rev-parse", "HEAD")
    write(repo, "module.py", "VALUE = 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "remote advances")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "--detach", baseline)
    write(repo, "README.md", "local prose\n")
    code, result = report(repo, "--backstop", "ci")
    assert code == 0
    assert result["base"] == baseline


def test_mixed_production_and_test_change_is_not_test_only(repo: Path) -> None:
    write(repo, "module.py", "VALUE = 2\n")
    write(repo, TESTS + "test_module.py", "def test_new(): assert True\n")
    assert report(repo, "--backstop", "ci")[0] == 10


def test_deleted_owned_data_requires_full(repo: Path) -> None:
    write(repo, TESTS + "test_module/data.json", "{}")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "owned baseline")
    (repo / (TESTS + "test_module/data.json")).unlink()
    write(repo, TESTS + "test_module.py", "def test_new(): assert True\n")
    assert report(repo, "--base", "HEAD", "--backstop", "ci")[0] == 10


def test_selection_only_modules_and_no_unmatched_globs(repo: Path) -> None:
    write(repo, TESTS + "baseline.json", '{"mentions": "README.md"}')
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "baseline reference")
    write(repo, TESTS + "test_reference.py", 'NAME = "README.md"\n')
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "test reference")
    write(repo, "README.md", "new\n")
    write(repo, "unmatched.py", '"""new"""\n')
    git(repo, "add", "unmatched.py")
    git(repo, "commit", "-qm", "unmatched baseline")
    write(repo, "unmatched.py", '"""changed"""\n')
    code, result = report(repo, "--base", "HEAD", "--backstop", "ci")
    assert code == 0
    assert TESTS + "test_reference.py" in result["modules"]
    assert TESTS + "baseline.json" not in result["modules"]
    assert all("*" not in path for path in result["modules"])
    assert result["modules"] == sorted(set(result["modules"]))


@pytest.mark.parametrize(
    "body,status",
    [
        ("def test_ok(): assert True\n", 0),
        ("def test_bad(): assert False\n", 1),
        ("VALUE = 1\n", 5),
    ],
)
def test_run_member_interpreter_status_and_json(
    repo: Path, body: str, status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    for path in ("test_module.py", "test_no_retired.py", "test_glossary_terms.py"):
        write(repo, TESTS + path, body)
    # Do not isolate package settings; only prevent host-specific pytest plugins.
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    code, result = report(repo, "--backstop", "ci", "--run")
    assert code == status
    assert result["verdict"] == "targeted"


def test_empty_selection_is_not_green(repo: Path) -> None:
    for path in (repo / TESTS).glob("test_*.py"):
        path.unlink()
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "no tests baseline")
    write(repo, "README.md", "new\n")
    result = command(repo, "--base", "HEAD", "--backstop", "ci", "--run")
    assert result.returncode == 1
    assert "empty test selection" in result.stderr


def test_symlink_and_deterministic_first_reason(repo: Path) -> None:
    write(repo, "z.py", "VALUE = 2\n")
    write(repo, "a.sh", "echo no\n")
    code, result = report(repo, "--backstop", "ci")
    assert code == 10
    assert result["reason"].startswith("a.sh:")
    (repo / "a.sh").unlink()
    (repo / "z.py").unlink()
    (repo / "linked.md").symlink_to("README.md")
    assert report(repo, "--backstop", "ci")[0] == 10


def test_outside_repository_error(tmp_path: Path) -> None:
    result = command(tmp_path, "--backstop", "ci")
    assert result.returncode == 1
    assert not result.stdout


def test_command_is_not_in_distributed_package() -> None:
    assert COMMAND.parent.name == "python"
    assert COMMAND.parent.joinpath("git_loopy").is_dir()
    manifest = COMMAND.parent.joinpath("pyproject.toml").read_text()
    assert 'packages = ["git_loopy"]' in manifest
    assert "targeted_tests" not in manifest


def test_policy_and_chain_have_single_documentary_sources() -> None:
    root = COMMAND.parents[2]
    agents = (root / "AGENTS.md").read_text()
    rules = (root / "docs/agents/feedback-loops.md").read_text()
    hooks = (root / "docs/agents/chain-hooks.md").read_text()
    assert "targeted_tests.py" in agents
    assert "ast.dump" not in agents
    assert "ast.dump" in rules
    assert "test_reclaims_setup_children_not_just_their_parent[cancelled-init]" in rules
    assert "chain.sh complete" not in agents
    assert "chain.sh complete" in hooks
    assert "docs/agents/chain-hooks.md" in agents
    assert os.environ.get("HOME") is not None
