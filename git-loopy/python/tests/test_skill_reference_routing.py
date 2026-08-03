"""Every Skill reference leads to the external source of record (#339).

ADR-0023 named `bradcstevens/git-loopy-skills` the source of record and
ADR-0025 made a Run resolve Skills from **one** place: the catalog git-loopy
installs from that pin into its own config scope. Neither a distribution nor
the consuming repository supplies a Skill any more.

The documentation is the surface an operator and a contributor actually act
on, so it is the surface that has to say so. These guards hold the routing:

* **Nothing points into a repository-root Skill tree.** A first-party document
  that links at `.copilot/skills/<name>/SKILL.md` is presenting that tree as
  canonical, which is exactly the claim ADR-0025 retired -- and, because the
  tree is nobody's source of record, the link is already prone to naming a
  Skill that no supported install provides.
* **The catalog table names the catalog.** Every Skill the front door lists
  resolves in the pinned external repository, so a reader can install what
  they just read about.
* **The supported install and update flow is the external catalog's own.**
  Guidance names the `skills` CLI, and relates it to `git-loopy init` rather
  than presenting the two as alternatives to each other.
* **Nothing claims a shipped catalog.** No document offers a packaged,
  vendored, or bundled Skill catalog, because no distribution carries one.
* **The links resolve.** Relative targets and in-document anchors in the Skill
  documentation point at something that exists.

Scanning the *git-tracked* surface (not a filesystem walk) keeps the guards
deterministic regardless of what untracked acquisitions, virtualenvs, or
scratch files happen to sit in a working tree.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

#: The external catalog: the one source of record for git-loopy's Skills.
SKILL_CATALOG_REPOSITORY = "bradcstevens/git-loopy-skills"

#: A Markdown inline link's target, e.g. ``[text](target)``.
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

#: A link into a repository-root Skill tree, in any relative spelling.
_REPOSITORY_SKILL_TREE = re.compile(r"(?:^|/)\.copilot/skills(?:/|$)")

#: Repo-relative POSIX prefixes whose Skill references are not navigation.
_EXEMPT_PREFIXES: tuple[str, ...] = (
    ".copilot/",  # the tree's own contents; #340 removes it
    "docs/adr/",  # architecture decision records are immutable history
    ".reference/",  # third-party notes captured verbatim
)


def _find_repo_root() -> Path | None:
    """The first ancestor holding both ``docs/adr/`` and ``CONTEXT.md``."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docs" / "adr").is_dir() and (parent / "CONTEXT.md").is_file():
            return parent
    return None


def _tracked_markdown(repo_root: Path) -> list[str]:
    """Repo-relative POSIX paths of every git-tracked Markdown file."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z", "*.md"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - no git
        return []
    return [path for path in completed.stdout.split("\0") if path]


def _first_party_markdown(repo_root: Path) -> list[str]:
    """Tracked Markdown that is published guidance rather than history."""
    guard_rel = Path(__file__).resolve().relative_to(repo_root).as_posix()
    return [
        rel
        for rel in _tracked_markdown(repo_root)
        if rel != guard_rel and not rel.startswith(_EXEMPT_PREFIXES)
    ]


@pytest.fixture(scope="module")
def repo_root() -> Path:
    root = _find_repo_root()
    if root is None:  # pragma: no cover - installed-wheel run
        pytest.skip("repo root not found (installed-wheel run) -- nothing to scan")
    if not _tracked_markdown(root):  # pragma: no cover - no git
        pytest.skip("git-tracked file list unavailable -- nothing to scan")
    return root


def test_no_first_party_doc_links_into_a_repository_skill_tree(
    repo_root: Path,
) -> None:
    """A repository tree is nobody's source of record, so nothing navigates to it."""
    documents = _first_party_markdown(repo_root)
    assert len(documents) >= 20, (
        f"guard scanned only {len(documents)} documents -- the scan looks broken"
    )

    failures: list[str] = []
    for rel in documents:
        text = (repo_root / rel).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for target in _MARKDOWN_LINK.findall(line):
                if _REPOSITORY_SKILL_TREE.search(target):
                    failures.append(f"{rel}:{lineno}: links at {target}")

    assert not failures, (
        "first-party guidance must route Skill references to "
        f"{SKILL_CATALOG_REPOSITORY}, the source of record (ADR-0023), rather "
        "than to a repository-root `.copilot/skills` tree -- which git-loopy "
        "neither ships nor reads (ADR-0025). Offending links:\n  "
        + "\n  ".join(failures)
    )


#: Where a Skill reference must lead: the external catalog's own tree.
_CATALOG_SKILL_URL = re.compile(
    r"https://github\.com/" + SKILL_CATALOG_REPOSITORY + r"/tree/[^/]+/skills/([a-z0-9-]+)"
)


def _documented_skills(repo_root: Path) -> dict[str, str]:
    """Skill name -> link target, for every Skill the front door's table lists."""
    text = (repo_root / "README.md").read_text(encoding="utf-8")
    documented: dict[str, str] = {}
    for line in text.splitlines():
        row = re.match(r"\|\s*\[`/([a-z0-9-]+)`\]\(([^)]+)\)\s*\|", line)
        if row:
            documented[row.group(1)] = row.group(2)
    return documented


def test_the_front_door_documents_every_required_skill(repo_root: Path) -> None:
    """A Run cannot start without these, so the front door has to name them."""
    from git_loopy.prompt import resolve_required_skills

    required = resolve_required_skills(
        (repo_root / "git-loopy" / "PROMPT.md").read_text(encoding="utf-8")
    ).required_skills
    assert required, "the packaged Run instructions declare no Required Skills"

    documented = _documented_skills(repo_root)
    assert documented, "no Skill table found in README.md -- the scan looks broken"

    missing = sorted(name for name in required if name not in documented)
    assert not missing, (
        "the Run instructions require these Skills, so a reader must be able to "
        f"find them on the front door: {missing}"
    )


def test_every_documented_skill_links_to_the_external_catalog(
    repo_root: Path,
) -> None:
    """The table is a route to the source of record, not a route to this tree."""
    documented = _documented_skills(repo_root)
    assert documented, "no Skill table found in README.md -- the scan looks broken"

    stray = sorted(
        f"/{name} -> {target}"
        for name, target in documented.items()
        if (match := _CATALOG_SKILL_URL.match(target)) is None or match.group(1) != name
    )
    assert not stray, (
        f"every documented Skill must link at {SKILL_CATALOG_REPOSITORY}, under "
        f"its own name: {stray}"
    )


def test_every_documented_skill_exists_in_the_pinned_catalog(
    repo_root: Path,
) -> None:
    """A route that names a Skill the catalog does not carry is a dead end.

    This is the guard that has teeth about *content* rather than about link
    shape, and it can only run where the pinned revision is actually on disk:
    acquiring it reaches the network, which AGENTS.md keeps out of the gate on
    purpose. On a developer machine that has run ``python -m
    git_loopy.skill_source`` it is a real tripwire; in CI it stands down rather
    than making the suite depend on an upstream nobody here controls.
    """
    from git_loopy.skill_source import (
        DEFAULT_CHECKOUT,
        is_previous_acquisition,
        read_skill_source_pin,
        validate_skill_source,
    )

    checkout = repo_root / DEFAULT_CHECKOUT
    if not is_previous_acquisition(checkout):
        pytest.skip(
            "no acquisition of the pinned catalog on disk -- run "
            "`uv run --project git-loopy/python python -m git_loopy.skill_source`"
        )

    pin = read_skill_source_pin()
    catalog = set(validate_skill_source(pin, checkout).skills)
    assert catalog, "the acquired catalog carries no Skills"

    documented = _documented_skills(repo_root)
    unknown = sorted(name for name in documented if name not in catalog)
    assert not unknown, (
        f"documented but absent from {pin.repository} @ {pin.short_revision}, so "
        f"no supported install provides them: {unknown}"
    )

    undocumented = sorted(catalog - set(documented))
    assert not undocumented, (
        f"carried by {pin.repository} @ {pin.short_revision} but missing from the "
        f"front door's table: {undocumented}"
    )


#: The Skill documentation an operator or contributor is routed through.
_SKILL_DOCUMENTS: tuple[str, ...] = (
    "README.md",
    "docs/skills-setup.md",
    "docs/skill-catalog-source.md",
    "docs/customization.md",
    "docs/workflow.md",
    "git-loopy/shell/README.md",
    "git-loopy/powershell/README.md",
    "git-loopy/python/README.md",
)


def _heading_slug(title: str) -> str:
    """GitHub's heading slug: drop punctuation, lower-case, spaces to hyphens."""
    stripped = re.sub(r"[^\w\s-]", "", title.replace("`", "")).strip().lower()
    return stripped.replace(" ", "-")


def test_the_supported_install_flow_is_the_external_catalogs_own(
    repo_root: Path,
) -> None:
    """Typing a slash command yourself means installing from the source of record."""
    setup = (repo_root / "docs" / "skills-setup.md").read_text(encoding="utf-8")

    assert f"npx skills add {SKILL_CATALOG_REPOSITORY}" in setup, (
        "setup guidance must use the external catalog's own documented `skills` "
        "CLI install flow"
    )
    assert "npx skills update" in setup, (
        "an operator who installed the catalog needs the documented way to "
        "update it, not just the way to obtain it once"
    )
    assert "git-loopy init" in setup, (
        "the `skills` CLI flow has to be related to `git-loopy init` rather than "
        "presented as an alternative to it"
    )
    assert "cp -R ~/.config/git-loopy/skills" not in setup, (
        "copying git-loopy's installed catalog sideways forks it from the pin; "
        "install from the source of record instead"
    )


#: The glossary entry whose *Avoid* list names the retired catalog vocabulary.
_INSTALLED_CATALOG_ENTRY = "**Installed catalog**:"

#: Claims the guard below must catch, whatever the glossary spells its list as.
#: Each asserts the thing ADR-0025 deleted; the last three are the
#: Markdown-decorated spellings a plain-prose scan walks straight past.
_RETIRED_CLAIMS: tuple[str, ...] = (
    "the packaged skill catalog is refreshed at preflight",
    "install the packaged workflow skill catalog",
    "git-loopy falls back to its vendored skills",
    "a vendored catalog ships inside the wheel",
    "the bundled skill catalog wins",
    "the release carries a packaged fallback",
    "install the packaged **workflow skill** catalog",
    "the **bundled** skill catalog",
    "configuring the vendored skills",
)


def _forbidden_catalog_vocabulary(repo_root: Path) -> tuple[str, ...]:
    """The words CONTEXT.md's **Installed catalog** entry tells writers to avoid.

    Read out of the glossary rather than restated here, so the guard cannot
    drift from the entry it enforces: a term added to that *Avoid* list becomes
    a tripwire in the same commit that adds it. Restating the list is exactly
    what let "vendored skills" survive in `docs/workflow.md` while a hand-rolled
    regex naming "vendored catalog" reported the whole surface clean.
    """
    text = (repo_root / "CONTEXT.md").read_text(encoding="utf-8")
    entry = text[text.index(_INSTALLED_CATALOG_ENTRY) :]
    avoid = re.search(r"(?m)^_Avoid_:\s*(.+?)\.\s*$", entry)
    assert avoid, "the **Installed catalog** glossary entry carries no _Avoid_ list"
    return tuple(term.strip() for term in avoid.group(1).split(",") if term.strip())


def _retired_claim_pattern(vocabulary: tuple[str, ...]) -> re.Pattern[str]:
    """The *Avoid* list, read as a cross-product rather than three spellings.

    Each avoided term is an adjective about provenance ("packaged", "vendored",
    "bundled") applied to a noun for the thing ("fallback", "skills",
    "catalog"), and *any* pairing of the two makes the same retired claim -- so
    the guard refuses all nine rather than only the three the glossary happens
    to write out.

    Nouns are taken verbatim, pluralised only where the glossary states them in
    the singular. Singularising "skills" instead would condemn "the packaged
    Required Skill list", which is a true statement about the packaged
    `PROMPT.md` (ADR-0006) and no claim about Skills at all.
    """
    provenance: set[str] = set()
    subjects: set[str] = set()
    for term in vocabulary:
        adjective, _, noun = term.partition(" ")
        assert noun, f"expected an '<adjective> <noun>' term, got {term!r}"
        provenance.add(re.escape(adjective.lower()))
        subjects.add(re.escape(noun.lower()) + ("" if noun.endswith("s") else "s?"))

    # Any intervening words may only be qualifiers that leave it the *same*
    # claim ("packaged **workflow skill** catalog"), each free to carry
    # Markdown emphasis so that bolding one does not launder it. Admitting
    # arbitrary words instead would condemn "the packaged instructions'
    # Required Skills", which is true of the packaged `PROMPT.md` (ADR-0006).
    return re.compile(
        r"\b(?:" + "|".join(sorted(provenance)) + r")\b"
        r"(?:\W+(?:workflow|agent|skill)){0,2}\W+"
        r"(?:" + "|".join(sorted(subjects)) + r")\b",
        re.IGNORECASE,
    )


def test_no_first_party_doc_uses_the_retired_catalog_vocabulary(
    repo_root: Path,
) -> None:
    """A distribution carries no Skills, so no document may say it does."""
    vocabulary = _forbidden_catalog_vocabulary(repo_root)
    assert len(vocabulary) >= 3, (
        f"the _Avoid_ list parsed as {vocabulary} -- the scan looks broken"
    )
    claims = _retired_claim_pattern(vocabulary)

    missed = [claim for claim in _RETIRED_CLAIMS if not claims.search(claim)]
    assert not missed, (
        "the vocabulary derived from CONTEXT.md's **Installed catalog** entry "
        "no longer catches these retired claims, so the scan below would pass "
        f"straight over them: {missed}"
    )

    failures = [
        f"{rel}:{lineno}: {line.strip()[:110]}"
        for rel in _first_party_markdown(repo_root)
        for lineno, line in enumerate(
            (repo_root / rel).read_text(encoding="utf-8").splitlines(), start=1
        )
        # A glossary *Avoid* line's whole job is to write the retired
        # vocabulary down, so it is the one line exempt from it -- rather than
        # the whole of CONTEXT.md, which is guidance like any other.
        if not line.lstrip().startswith("_Avoid_:") and claims.search(line)
    ]
    assert not failures, (
        "a git-loopy distribution carries no Skills -- it carries the pin, and "
        "the catalog is installed from the external source of record "
        f"(ADR-0025). CONTEXT.md tells writers to avoid {list(vocabulary)}. "
        "Offending claims:\n  " + "\n  ".join(failures)
    )


def test_every_anchor_in_the_skill_documentation_resolves(repo_root: Path) -> None:
    """A route that lands on a file or heading that is gone is not a route."""
    link = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
    heading = re.compile(r"(?m)^#{1,6}\s+(.*)$")

    failures: list[str] = []
    for rel in _SKILL_DOCUMENTS:
        source = repo_root / rel
        text = source.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for target in link.findall(line):
                if target.startswith(("http", "mailto:")):
                    continue
                path, _, fragment = target.partition("#")
                destination = (source.parent / path).resolve() if path else source
                if not destination.exists():
                    failures.append(f"{rel}:{lineno}: {target} names no file")
                    continue
                if not fragment or destination.suffix != ".md":
                    continue
                slugs = {
                    _heading_slug(match.group(1))
                    for match in heading.finditer(
                        destination.read_text(encoding="utf-8")
                    )
                }
                if fragment not in slugs:
                    failures.append(f"{rel}:{lineno}: {target} names no heading")

    assert not failures, (
        "these links land nowhere:\n  " + "\n  ".join(failures)
    )


def test_no_first_party_doc_installs_skills_by_copying_a_repository_tree(
    repo_root: Path,
) -> None:
    """Copying a checkout forks the catalog from the pin that names it.

    A ``cp -R`` is not a Markdown link, so the routing guard above cannot see
    it -- and it is the more dangerous form, because the reader ends up with
    Skills whose revision nothing records.
    """
    copying = re.compile(
        r"(?:cp\s+-\w+\s+|copy\s+`?)\.?[\w./]*\.copilot/skills|\.copilot/skills/\*"
    )
    failures = [
        f"{rel}:{lineno}: {line.strip()[:110]}"
        for rel in _first_party_markdown(repo_root)
        for lineno, line in enumerate(
            (repo_root / rel).read_text(encoding="utf-8").splitlines(), start=1
        )
        if copying.search(line)
    ]
    assert not failures, (
        "Skills are installed from the pinned external catalog -- by "
        "`git-loopy init` for a Run, or by `npx skills add` for Copilot CLI -- "
        "never by copying a repository tree. Offending instructions:\n  "
        + "\n  ".join(failures)
    )


def test_no_first_party_doc_offers_editable_skill_copies(repo_root: Path) -> None:
    """Setup writes Config and a prompt override -- never a Skill (ADR-0025).

    The installed catalog is git-loopy's to own: a hand-edited Skill is
    *repaired* on the next Run, so a document that offers editable copies is
    offering an edit the next Run silently reverts.
    """
    offers = re.compile(r"editable\b(?:\W+\w[\w-]*){0,6}\W+skills\b", re.IGNORECASE)
    failures = [
        f"{rel}:{lineno}: {line.strip()[:110]}"
        for rel in _first_party_markdown(repo_root)
        for lineno, line in enumerate(
            (repo_root / rel).read_text(encoding="utf-8").splitlines(), start=1
        )
        if offers.search(line)
    ]
    assert not failures, (
        "`git-loopy init` scaffolds an editable `PROMPT.md` and writes Config; "
        "it installs the Skill catalog rather than handing out copies to edit "
        "(ADR-0025). Offending offers:\n  " + "\n  ".join(failures)
    )


def test_every_exempt_prefix_names_something_that_exists(repo_root: Path) -> None:
    """A prefix naming nothing exempts nothing -- and hides that it exempts nothing.

    `docs/feature-requests/` sat in this list while the intake actually lives in
    `docs/features/`, so the list read as narrower than the scan really was.
    Either way round the mismatch is a lie about coverage, so the list is held
    to naming real paths.
    """
    missing = [
        prefix for prefix in _EXEMPT_PREFIXES if not (repo_root / prefix).is_dir()
    ]
    assert not missing, (
        f"these exemptions name no directory, so they exempt nothing: {missing}"
    )


def test_every_orchestrator_onboarding_names_the_supported_install(
    repo_root: Path,
) -> None:
    """Each port routes to the install that actually serves *it*.

    A shell or PowerShell Run invokes `copilot`, which resolves Skills from
    Copilot CLI's own sources. git-loopy's **installed catalog** is passed to
    the client by the Python Runner alone (`loop.py`'s `installed_skills_dir`),
    and these ports report `enabled_skills` as an unsupported Config surface --
    so telling their operators that a machine-wide install "serves this
    Orchestrator too" routes them to an install their Runs never read.
    """
    for rel in ("git-loopy/shell/README.md", "git-loopy/powershell/README.md"):
        text = (repo_root / rel).read_text(encoding="utf-8")
        assert f"npx skills add {SKILL_CATALOG_REPOSITORY}" in text, (
            f"{rel} must route its Skills onboarding through the external "
            "catalog's own install, which is what a `copilot` session this "
            "Orchestrator starts can actually resolve"
        )
        assert "git-loopy init" in text, (
            f"{rel} must relate that install to `git-loopy init`, which writes "
            "the Config this Orchestrator reads"
        )
        assert "serves this Orchestrator too" not in text, (
            f"{rel} claims git-loopy's installed catalog serves this port; only "
            "the Python Runner passes it to the Copilot client (ADR-0025)"
        )


def test_the_skills_reference_distinguishes_all_three_sources(
    repo_root: Path,
) -> None:
    """Three sources, three answers to "who reads this?" -- kept apart.

    Collapsing them is what made "install the skills" ambiguous in the first
    place: the external repository is the source of record, the installed
    catalog is the only thing a Run reads, and Copilot CLI's own sources are
    what answer a slash command an operator types -- bounded by the Skill
    policy, and supplying nothing to a Run.
    """
    text = (repo_root / "docs" / "customization.md").read_text(encoding="utf-8")
    reference = text[text.index("## Skills reference") :]
    reference = reference[: reference.index("\n## ", 1)]

    for phrase, why in (
        (SKILL_CATALOG_REPOSITORY, "the external source of record"),
        ("<config-home>/git-loopy/skills/", "the installed catalog a Run reads"),
        ("~/.copilot/skills/", "Copilot CLI's own personal source"),
        ("skill-policy.md", "the closed-world policy that bounds Copilot's sources"),
        ("0025-installed-skill-catalog", "the decision that says a Run reads one source"),
    ):
        assert phrase in reference, (
            f"the Skills reference must name {why} ({phrase!r}) so the three "
            "Skill sources stay distinguishable"
        )
