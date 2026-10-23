"""The host-load terms distinguish added load from the Survivor check (#874)."""

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("term", "claims"),
    [
        (
            "Deliberate load",
            (
                "Load added on top of a command to reproduce or measure something",
                "never a declared feedback loop or the parallelism its runner starts",
                "_Avoid_:",
            ),
        ),
        (
            "Survivor check",
            (
                "before finishing and after any interrupted command",
                "processes its commands started that are still running",
                "ends only attributable survivors by PID",
                "reports what it ended",
                "names on the issue any survivor it cannot attribute",
                "_Avoid_: cleanup, reap, sweep.",
            ),
        ),
    ],
)
def test_glossary_defines_host_load_terms(term: str, claims: tuple[str, ...]) -> None:
    root = Path(__file__).resolve().parents[3]
    glossary = (root / "GLOSSARY.md").read_text(encoding="utf-8")
    heading = f"**{term}**:\n"
    assert heading in glossary
    entry = " ".join(glossary.split(heading, 1)[1].split("\n\n", 1)[0].split())
    for claim in claims:
        assert claim in entry
