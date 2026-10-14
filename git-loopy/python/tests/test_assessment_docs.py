"""The Release assessment guide and glossary stay equal to the shipped service (#766)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from git_loopy.assessment_github import ENDPOINT_PERMISSIONS, READ_PERMISSIONS, GitHubReads

ROOT = Path(__file__).resolve().parents[3]
GUIDE = (ROOT / "docs" / "release-assessment.md").read_text(encoding="utf-8")
GLOSSARY = (ROOT / "GLOSSARY.md").read_text(encoding="utf-8")


def _documented() -> list[tuple[str, str, str]]:
    block = GUIDE.split("<!-- endpoint-matrix:start -->")[1].split("<!-- endpoint-matrix:end -->")[0]
    rows = re.findall(r"^\| (GET|POST) \| `([^`]+)` \| ([^|]+) \|$", block, re.M)
    return [(m, p, perm.strip()) for m, p, perm in rows]


def test_documented_endpoints_equal_the_services_fixed_list() -> None:
    assert _documented() == list(ENDPOINT_PERMISSIONS)


def test_the_token_asks_only_for_permissions_some_endpoint_needs() -> None:
    needed = {perm.lower().replace(" ", "_") for _, _, perm in ENDPOINT_PERMISSIONS}
    assert set(READ_PERMISSIONS) == needed - {"app_jwt"}
    assert {"checks", "statuses"}.isdisjoint(READ_PERMISSIONS)
    assert "**Checks** or **Commit statuses**" in GUIDE


@pytest.mark.anyio
async def test_every_listed_repository_read_is_accepted_and_nothing_else() -> None:
    seen = []

    class Transport:
        async def request(self, method, path, **kwargs):
            seen.append((method, path))
            from git_loopy.assessment_github import Response
            return Response(200, {})

    sha = "a" * 40
    reads = GitHubReads("o/r", Transport(), token="t")
    fills = {"{ref}": "main", "{n}": "7", "{base}": sha, "{sha}": "b" * 40}
    for method, path, _ in ENDPOINT_PERMISSIONS:
        if method != "GET" or not path.startswith("/repos/{repository}") or path.endswith("/installation"):
            continue
        suffix = path.removeprefix("/repos/{repository}")
        for k, v in fills.items():
            suffix = suffix.replace(k, v)
        await reads._get(suffix, {}, "read", "src")
    assert len(seen) == 10
    with pytest.raises(Exception):
        await reads._get("/hooks", {}, "read", "src")


@pytest.mark.parametrize(
    "term",
    ["Release assessment", "Assessment verdict", "Assessment policy", "Review packet"],
)
def test_glossary_entry_carries_the_terms_to_avoid(term: str) -> None:
    entry = GLOSSARY.split(f"**{term}**:")[1].split("\n\n")[0]
    for avoided in ("release readiness", "ready to release", "go/no-go", "approval", "release gate"):
        assert avoided in entry.split("_Avoid_:")[1]
