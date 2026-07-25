"""Import-discipline coverage for the toolkit-neutral Dashboard projection."""

from __future__ import annotations

import ast
from pathlib import Path

from git_loopy.interactive import view_model


def test_view_model_module_has_no_renderer_dependency() -> None:
    source = Path(view_model.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {
        "__future__",
        "decimal",
        "typing",
        "git_loopy.interactive.state",
        "git_loopy.pricing",
        "git_loopy.ui.summary",
    }
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            seen.add(node.module)

    assert not seen - allowed
    assert "textual" not in seen
    assert "rich" not in seen
