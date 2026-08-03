"""One extractor for the request templates a Skill documents.

A Skill is a prompt, so the only honest way to pin what it publishes is to make
its documented request *executable*: each SKILL.md carries its completion request
as a fenced JSON template behind a ``<!-- continuation-request: NAME -->`` marker,
and the Transition-owner suites extract that exact template, substitute its
``"<placeholder>"`` values with one scenario's durable identifiers, and drive the
real native command.

That only holds while there is one extractor. Four suites each carrying their own
copy of the marker regexp is four chances for the thing that reads the contract to
disagree with the thing that reads the contract, which is the failure the pattern
exists to prevent.

**Where these prompts live, and why they are a fixture.** They used to be read out
of this checkout's root ``.copilot/skills/``, which #340 removed: under ADR-0025
git-loopy reads Skills from the catalog it installs from the pin and from nowhere
else, so a tracked tree here was a catalog no Run consults. What is left behind is
narrower and honest about itself -- the prompts that carry *git-loopy's own*
Continuation contract, which the pinned revision does not yet carry (#341). It is
a test fixture, not a Skill source: nothing installs it, no Run resolves against
it, and Copilot CLI does not discover it. When #341 publishes these upstream, the
fixture's replacement is a guard over the acquired revision.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: The prompts carrying git-loopy's Continuation contract. Membership *is* a
#: claim: a Skill is here exactly when it documents a request against the native
#: ``git-loopy continuation`` command.
CONTRACT_SKILLS_DIR = Path(__file__).parent / "fixtures" / "continuation-skills"

TEMPLATE_RE = re.compile(
    r"<!-- continuation-request: (?P<name>[a-z-]+) -->\s*```json\n(?P<body>.*?)```",
    re.DOTALL,
)


def skill_text(skill: str, *, skills_dir: Path = CONTRACT_SKILLS_DIR) -> str:
    """Return one Skill's prompt as written."""
    return (skills_dir / skill / "SKILL.md").read_text(encoding="utf-8")


def templates(skill: str, *, skills_dir: Path = CONTRACT_SKILLS_DIR) -> dict[str, str]:
    """Return every named request template documented by one Skill."""
    return {
        match.group("name"): match.group("body")
        for match in TEMPLATE_RE.finditer(skill_text(skill, skills_dir=skills_dir))
    }


def template(
    skill: str, name: str, *, skills_dir: Path = CONTRACT_SKILLS_DIR
) -> dict[str, Any]:
    """Return one named request template, parsed."""
    documented = templates(skill, skills_dir=skills_dir)
    assert name in documented, (
        f"{skill}/SKILL.md documents no <!-- continuation-request: {name} --> "
        f"template; it documents {sorted(documented)}"
    )
    return json.loads(documented[name])


def fill(value: Any, bindings: dict[str, Any]) -> Any:
    """Substitute a template's ``<placeholder>`` values with durable identifiers.

    A whole-string placeholder takes the binding's own type, so ``"<map-issue>"``
    becomes the integer issue number the contract requires; a placeholder embedded
    in prose (an Instruction, say) is substituted textually.
    """
    if isinstance(value, dict):
        return {key: fill(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [fill(item, bindings) for item in value]
    if isinstance(value, str):
        for name, binding in bindings.items():
            if value == f"<{name}>":
                return binding
        for name, binding in bindings.items():
            value = value.replace(f"<{name}>", str(binding))
        return value
    return value
