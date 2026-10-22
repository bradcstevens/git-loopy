"""Bold marks a glossary entry in this repository's Markdown (#943).

Capitalised bold spans of up to four words that contain a glossary term must
themselves be entries. Matching ignores case and accepts plurals and possessives;
code, links, digits and sentence punctuation are outside this narrow heuristic.
Avoid lines remain reviewer judgement, not a forbidden-phrase dictionary.

The tracked-tree ratchet needs no history (including in depth-one CI checkouts).
Existing file/phrase pairs are grandfathered in glossary_bold_baseline.json.
Never add new exceptions: use the canonical glossary term, add a glossary entry
(under an "(accepted design)" heading for an unshipped concept), or drop the bold.
Remove stale baseline pairs when their last occurrence disappears.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _term_forms(glossary: str) -> set[str]:
    heads = re.findall(
        r"^\*\*([^*\n]+)\*\*(?: _\(historical\)_)?:", glossary, re.MULTILINE
    )
    forms: set[str] = set()
    for head in heads:
        term = _normalise(head)
        if term.endswith("y") and len(term) > 1 and term[-2] not in "aeiou":
            plural = term[:-1] + "ies"
        elif term.endswith(("s", "x", "z", "ch", "sh")):
            plural = term + "es"
        else:
            plural = term + "s"
        for form in (term, plural):
            forms.add(form)
            for apostrophe in ("'", "’"):
                forms.add(form + apostrophe + "s")
                if form.endswith("s"):
                    forms.add(form + apostrophe)
    return forms


def _without_code(markdown: str) -> str:
    """Mask fenced/indented blocks and inline code, retaining a span barrier."""
    lines: list[str] = []
    fence: str | None = None
    for line in markdown.splitlines(keepends=True):
        if fence is not None:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + r"{"
                            + str(len(fence)) + r",}\s*", line):
                fence = None
            lines.append("\0\n")
        elif opening := re.match(r" {0,3}(`{3,}|~{3,})", line):
            fence = opening[1]
            lines.append("\0\n")
        elif line.startswith(("    ", "\t")):
            lines.append("\0\n")
        else:
            lines.append(line)
    return re.sub(r"(?<!`)(`+)(?!`)[\s\S]*?(?<!`)\1(?!`)", "`", "".join(lines))


def bold_compounds(markdown: str, glossary: str) -> set[str]:
    """Classify synthetic or file Markdown into normalised offending phrases."""
    forms = _term_forms(glossary)
    if not forms:
        raise AssertionError("No glossary entry heads found; the guard cannot run")
    contains_term = re.compile(
        r"(?<![\w-])(?:" + "|".join(re.escape(term) for term in sorted(forms))
        + r")(?![\w-])"
    )
    hits: set[str] = set()
    for match in re.finditer(
        r"(?<!\\)(\*\*|__)(?!\s)([^*_]+?)(?<!\s)\1", _without_code(markdown)
    ):
        span = match[2]
        phrase = _normalise(span)
        if (
            not re.search(r"\n\s*\n", span)
            and 1 <= len(phrase.split()) <= 4
            and span[0].isupper()
            and all(char.isalpha() or char.isspace() or char in "-'’" for char in span)
            and phrase not in forms
            and contains_term.search(phrase)
        ):
            hits.add(phrase)
    return hits


def tracked_tree_hits(root: Path) -> set[tuple[str, str]]:
    """Read only tracked Markdown, except verbatim research captures."""
    glossary = (root / "GLOSSARY.md").read_text(encoding="utf-8")
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout.split("\0")
    return {
        (relative, phrase)
        for relative in tracked
        if relative.lower().endswith(".md") and not relative.startswith("docs/research/")
        for phrase in bold_compounds((root / relative).read_text(encoding="utf-8"), glossary)
    }


def assert_baseline(hits: set[tuple[str, str]], baseline: set[tuple[str, str]]) -> None:
    """Both additions and disappeared exceptions break the ratchet."""
    new = hits - baseline
    stale = baseline - hits
    details = [f"New: {file}: {phrase!r}" for file, phrase in sorted(new)]
    details += [f"Stale baseline entry: {file}: {phrase!r}" for file, phrase in sorted(stale)]
    assert not details, (
        "Bold marks a glossary entry in this repository's Markdown.\n"
        + "\n".join(details)
        + "\nFor a new phrase: use the canonical glossary term; add a glossary entry "
        '(under an "(accepted design)" heading if the concept has not shipped); '
        "or drop the bold. Do not grow the baseline.\n"
        "For a stale baseline entry: remove the file/phrase pair from the baseline."
    )


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("**Integration Recovery**", {"integration recovery"}),
        ("**New recovery**", {"new recovery"}),
        ("**New Recoveries**", {"new recoveries"}),
        ("**New Recovery’s**", {"new recovery’s"}),
        ("**New Agents'**", {"new agents'"}),
        ("**New Agent profiles**", {"new agent profiles"}),
        ("**New Board**", {"new board"}),
        ("**New Waves**", {"new waves"}),
        ("**One Two Three Recovery**", {"one two three recovery"}),
        ("**not** **Supersedes** **PowerShell**", set()),
        ("**Recovery** **RECOVERY** **recovery** **Recoveries**", set()),
        ("**Agent profiles** **AGENT PROFILES** **Agent profile’s**", set()),
        ("**Agents'** **Agents’** **Agent's** **Waves** **Boards**", set()),
        ("**integration Recovery**", set()),
        ("**One Two Three Four Recovery**", set()),
        ("**New Recovery2** **New Recovery.** **New Recovery!**", set()),
        ("**New `Recovery`** **New [Recovery](url)**", set()),
        ("**New Recovery-mode** **New Subagent**", set()),
        ("**Avoided phrase**", set()),
        ("__Integration Recovery__", {"integration recovery"}),
        ("**Integration\nRecovery**", {"integration recovery"}),
        ("**Integration\n \nRecovery**", set()),
        ("`**Integration Recovery**`", set()),
        ("``literal ` **Integration Recovery**``", set()),
        ("```md\n**Integration Recovery**\n```", set()),
        ("~~~md\n**Integration Recovery**\n~~~", set()),
        ("````md\n```\n**Integration Recovery**\n````", set()),
        ("```\n**Integration Recovery**", set()),
        ("    **Integration Recovery**\n", set()),
        (r"\*\*Integration Recovery\*\*", set()),
        (r"\**Integration Recovery**", set()),
    ],
)
def test_classifies_bold_spans(markdown: str, expected: set[str]) -> None:
    glossary = (
        "**Recovery**:\nA repair.\n_Avoid_: Avoided phrase.\n\n"
        "**Agent**:\nA session.\n\n**Agent profile**:\nA role.\n\n"
        "### Board (accepted design)\n\n**Board**:\nA view.\n\n"
        "**Wave** _(historical)_:\nA cohort.\n"
    )
    assert bold_compounds(markdown, glossary) == expected


def test_tracked_tree_ratchet_rejects_new_and_stale_pairs(tmp_path: Path) -> None:
    """Exercise the tracked surface, with no commit or merge-base available."""
    documents = {
        "GLOSSARY.md": "**Recovery**:\nA repair. See **Old Recovery**.\n",
        "README.md": "**Integration Recovery**\n**Integration Recovery**\n",
        "docs/releases/v1.md": "**New Recovery**\n",
        "docs/UPPER.MD": "**Upper Recovery**\n",
        "docs/research/capture.md": "**Captured Recovery**\n",
        "docstrings.py": '"""**Python Recovery**"""\n',
    }
    for relative, text in documents.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    (tmp_path / "untracked.md").write_text("**Untracked Recovery**", encoding="utf-8")

    baseline = {
        ("GLOSSARY.md", "old recovery"),
        ("README.md", "integration recovery"),
        ("docs/releases/v1.md", "new recovery"),
        ("docs/UPPER.MD", "upper recovery"),
    }
    hits = tracked_tree_hits(tmp_path)
    assert hits == baseline
    assert_baseline(hits, baseline)

    (tmp_path / "README.md").write_text(
        "**Integration Recovery**\n**Extra Recovery**", encoding="utf-8"
    )
    with pytest.raises(AssertionError, match="New"):
        assert_baseline(tracked_tree_hits(tmp_path), baseline)

    (tmp_path / "README.md").write_text("**Recovery**", encoding="utf-8")
    with pytest.raises(AssertionError, match="Stale"):
        assert_baseline(tracked_tree_hits(tmp_path), baseline)


def test_failure_names_the_pair_and_three_fixes() -> None:
    with pytest.raises(AssertionError) as failure:
        assert_baseline({("guide.md", "integration recovery")}, set())
    message = str(failure.value)
    for required in (
        "guide.md", "integration recovery", "use the canonical glossary term",
        "add a glossary entry", '"(accepted design)"', "drop the bold",
    ):
        assert required in message


def test_glossary_bold_compounds_in_tracked_markdown() -> None:
    root = Path(__file__).resolve().parents[3]
    pairs = json.loads(Path(__file__).with_name("glossary_bold_baseline.json").read_text(
        encoding="utf-8"
    ))
    baseline = {(file, phrase) for file, phrase in pairs}
    assert len(baseline) == len(pairs), "Duplicate baseline pairs must be removed"
    assert_baseline(tracked_tree_hits(root), baseline)
