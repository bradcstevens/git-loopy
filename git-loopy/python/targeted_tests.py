"""Contributor-only targeted Python test selection; see docs/agents/feedback-loops.md."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys

TESTS = "git-loopy/python/tests/"


class SelectorError(Exception):
    """Inspection could not establish a trustworthy selection."""


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise SelectorError(
            f"git {' '.join(args)}: {result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def test_module(path: str) -> bool:
    p = PurePosixPath(path)
    return path.startswith(TESTS) and p.name.startswith("test_") and p.suffix == ".py"


def normalized(source: bytes) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
    return ast.dump(tree)


@dataclass
class Report:
    base: str
    verdict: str
    change_class: str
    reason: str | None
    modules: list[str]
    backstop: str | None
    evidence: str


def inspect(root: Path, base: str | None, backstop: str | None) -> Report:
    if base is None:
        try:
            head = git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
            base = (
                git(root, "merge-base", "HEAD", head.decode().strip()).decode().strip()
            )
        except SelectorError as exc:
            raise SelectorError(
                "Cannot resolve the default base. Set origin/HEAD and fetch the "
                "default branch explicitly, or supply --base <revision>. "
                f"{exc}"
            ) from exc
    base = git(root, "rev-parse", "--verify", f"{base}^{{commit}}").decode().strip()
    # Disable rename detection so a deleted Python source remains disqualifying.
    raw = git(root, "diff", "--name-status", "-z", "--no-renames", base, "--")
    fields = raw.decode("utf-8").split("\0")
    changes = {path: status for status, path in zip(fields[:-1:2], fields[1:-1:2])}
    for path in (
        git(root, "ls-files", "--others", "--exclude-standard", "-z")
        .decode()
        .split("\0")
    ):
        if path:
            changes.setdefault(path, "A")
    tracked = set(git(root, "ls-files", "-z").decode().split("\0"))
    candidates = tracked | set(changes)
    modules = sorted(
        path
        for path in candidates
        if test_module(path)
        and (root / path).is_file()
        and not (root / path).is_symlink()
    )
    contents = {path: (root / path).read_bytes() for path in modules}
    changed_tests = {path for path in changes if test_module(path)}

    def owned_data(path: str) -> bool:
        p = PurePosixPath(path)
        if (
            not path.startswith(TESTS)
            or p.suffix in {".py", ".lock"}
            or set(p.parts) & {"fixtures", "fakes", "fake"}
            or p.name
            in {
                "pyproject.toml",
                "setup.cfg",
                "setup.py",
                "Pipfile",
                "Pipfile.lock",
                "package.json",
                "package-lock.json",
                "yarn.lock",
                "poetry.lock",
                "uv.lock",
                "Cargo.toml",
                "Cargo.lock",
            }
            or p.name.startswith(("requirements", "constraints"))
        ):
            return False
        owners = {
            module
            for module, content in contents.items()
            if p.name.encode() in content
            or path.startswith(str(PurePosixPath(module).with_suffix("")) + "/")
        }
        return bool(owners) and owners <= changed_tests

    prose_reason = test_reason = None
    for path, status in sorted(changes.items()):
        p = PurePosixPath(path)
        markdown = p.suffix.lower() == ".md"
        if (root / path).is_symlink():
            prose_reason = prose_reason or f"{path}: symlink change"
            test_reason = test_reason or f"{path}: symlink change"
            continue
        if markdown:
            continue
        if p.suffix == ".py":
            if status in {"A", "D"}:
                prose_reason = prose_reason or f"{path}: added or deleted Python file"
            else:
                try:
                    same = normalized(
                        git(root, "show", f"{base}:{path}")
                    ) == normalized((root / path).read_bytes())
                except (SyntaxError, ValueError):
                    same = False
                if not same:
                    prose_reason = (
                        prose_reason or f"{path}: non-docstring Python change"
                    )
        else:
            prose_reason = prose_reason or f"{path}: non-Markdown, non-Python change"
        if p.suffix == ".py" and status == "D":
            test_reason = test_reason or f"{path}: deleted existing Python file"
        elif not test_module(path) and not (status == "A" and owned_data(path)):
            test_reason = test_reason or f"{path}: not a test module or its owned data"
    if not changes:
        change_class, reason = "none", "no changes"
    elif prose_reason is None:
        change_class = (
            "Markdown-only"
            if all(PurePosixPath(p).suffix.lower() == ".md" for p in changes)
            else "docstring-or-prose"
        )
        reason = None
    elif test_reason is None:
        change_class, reason = "test-only", None
    else:
        change_class, reason = "ineligible", min(prose_reason, test_reason)
    if reason is None and backstop is None:
        reason = "an explicit --backstop ci or --backstop integration is required"
    selected = {
        module
        for module in modules
        if PurePosixPath(module).name.startswith(("test_no_", "test_glossary_"))
        or module in changed_tests
    }
    for path in changes:
        p = PurePosixPath(path)
        for module in modules:
            if p.name.encode() in contents[module] or (
                p.suffix == ".py"
                and PurePosixPath(module).name.startswith(f"test_{p.stem}")
            ):
                selected.add(module)
    selected_modules = sorted(selected)
    verdict = "full" if reason else "targeted"
    evidence = (
        f"Evidence: change-class={change_class}; "
        f"selected-modules={json.dumps(selected_modules, ensure_ascii=True)}; "
        f"full-suite-backstop={backstop or 'none'}"
    )
    return Report(
        base, verdict, change_class, reason, selected_modules, backstop, evidence
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", help="comparison commit (default: merge-base with origin/HEAD)"
    )
    parser.add_argument("--backstop", choices=("ci", "integration"))
    parser.add_argument("--json", action="store_true", help="emit one JSON report")
    parser.add_argument(
        "--run", action="store_true", help="run the selected modules with pytest"
    )
    args = parser.parse_args(argv)
    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        report = inspect(root, args.base, args.backstop)
        print(
            json.dumps(asdict(report))
            if args.json
            else (
                f"Verdict: {report.verdict}\n"
                f"Reason: {report.reason or report.change_class}\n"
                f"Selected modules: {json.dumps(report.modules)}\n{report.evidence}"
            ),
            flush=True,
        )
        if report.verdict == "full":
            return 10
        if args.run:
            if not report.modules:
                raise SelectorError("empty test selection; refusing to run pytest")
            return subprocess.run(
                [sys.executable, "-m", "pytest", *report.modules],
                cwd=root,
                check=False,
                stdout=sys.stderr if args.json else None,
            ).returncode
        return 0
    except (SelectorError, OSError, UnicodeError) as exc:
        print(f"targeted-tests: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
