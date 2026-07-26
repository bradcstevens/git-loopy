#!/usr/bin/env python3
"""Regenerate the canonical model-roster fixture from the pinned harness.

``conformance/model-roster.json`` is the offline fallback and the cross-language
contract for the kit's ``model -> accepted reasoning efforts`` decision. It was
hand-transcribed, and it drifted twice without a single test going red, because
every roster assertion in the suite is a mirror of the same hand-maintained
table: the mirrors drift together and stay green.

ADR-0019 settled what "correct" means for this file. Reasoning-effort capability
is **not** vendor data: ``models.list`` discards CAPI's advertised array and
reads a table hardcoded in the CLI bundle, so the roster is a function of the
**CLI version the SDK spawns** — not the CLI on the operator's ``PATH``. This
generator therefore reads the pinned harness through the SDK and stamps the
result with ``copilot._cli_version.CLI_VERSION``, so a stale fixture is
attributable to a Copilot CLI release instead of an unknown point in time.

Run it after bumping ``github-copilot-sdk`` (the pin bump and the regeneration
are **one atomic change**), then review and commit the result::

    uv run --project git-loopy/python python \\
        git-loopy/python/scripts/sync_model_roster.py

``--check`` reports drift without writing, naming the drifted ids and effort
sets. It is a **maintainer's** command: it needs Copilot authentication, so it
deliberately does **not** run in CI. No workflow in this repository holds those
credentials, and a live check that did would fail unrelated pull requests
whenever GitHub ships a model. The guard CI *can* run is offline and lives in
``tests/test_model_roster_sync.py``: it asserts the fixture names the pinned CLI
version, and that the committed bytes are exactly what :func:`render_document`
writes — so the fixture is provably generated rather than hand-edited.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

#: The fixture schema this generator writes. Bumped from 1 when roster entries
#: stopped being bare effort arrays and became ``{efforts, tiers}`` (ADR-0019).
SCHEMA_VERSION = 2

#: The Wrapper contract version whose decision this fixture pins. The decision
#: itself (model -> accepted efforts) is unchanged; the shape and provenance
#: around it are the fixture's own schema axis, above.
CONTRACT_VERSION = "1.1"

#: Every model runs at the default context tier. ``long_context`` is derived
#: from the presence of a long-context price block, which no surface publishes
#: directly -- it is a documented **proxy** (ADR-0019). Its failure mode is to
#: under-report, which routes into the existing warn-and-downgrade path.
CONTEXT_TIER_DEFAULT = "default"
CONTEXT_TIER_LONG = "long_context"

# scripts/ -> python/ -> git-loopy/
_GIT_LOOPY_DIR = Path(__file__).resolve().parents[2]
ROSTER_FIXTURE = _GIT_LOOPY_DIR / "conformance" / "model-roster.json"

#: Named in the drift message so a forgotten regeneration is self-correcting.
SYNC_COMMAND = (
    "uv run --project git-loopy/python python "
    "git-loopy/python/scripts/sync_model_roster.py"
)


@dataclass(frozen=True)
class RosterEntry:
    """One model's capability, as the pinned harness reports (or implies) it."""

    efforts: tuple[str, ...]
    tiers: tuple[str, ...]


def _long_context_block(model: Any) -> object | None:
    """The model's long-context price block, or ``None`` when it has none."""
    billing = getattr(model, "billing", None)
    prices = getattr(billing, "token_prices", None) if billing is not None else None
    return getattr(prices, "long_context", None) if prices is not None else None


def roster_from_models(models: Sequence[Any]) -> dict[str, RosterEntry]:
    """Project a live catalog into roster entries, preserving catalog order.

    ``models`` are duck-typed ``copilot.ModelInfo`` objects (attribute access
    only, mirroring :mod:`git_loopy.interactive.models`) so this projection is
    testable without a backend. Efforts are recorded **verbatim**: an empty
    tuple means the model is effort-*incapable*, which the harness enforces by
    hard-rejecting ``session.create``, so it is a value and not a gap.
    """
    roster: dict[str, RosterEntry] = {}
    for model in models:
        efforts = tuple(getattr(model, "supported_reasoning_efforts", None) or ())
        tiers = (CONTEXT_TIER_DEFAULT,)
        if _long_context_block(model) is not None:
            tiers = (CONTEXT_TIER_DEFAULT, CONTEXT_TIER_LONG)
        roster[str(getattr(model, "id"))] = RosterEntry(efforts=efforts, tiers=tiers)
    return roster


def render_document(
    roster: Mapping[str, RosterEntry],
    *,
    cli_version: str,
) -> str:
    """Serialise a roster into the exact text the fixture is committed as.

    Deterministic by construction: catalog order is preserved (so a diff shows
    the vendor's change, not a re-sort), one model renders on one line (so a
    vendor change is a one-line diff rather than forty), and nothing is derived
    from the clock or the environment. That is what lets an offline test assert
    the committed bytes are generator output.
    """
    entries = ",\n".join(
        f"    {json.dumps(model)}: {{"
        f'"efforts": {json.dumps(list(entry.efforts))}, '
        f'"tiers": {json.dumps(list(entry.tiers))}}}'
        for model, entry in roster.items()
    )
    return (
        "{\n"
        f'  "schema_version": {SCHEMA_VERSION},\n'
        f'  "contract_version": {json.dumps(CONTRACT_VERSION)},\n'
        f'  "cli_version": {json.dumps(cli_version)},\n'
        '  "roster": {\n'
        f"{entries}\n"
        "  }\n"
        "}\n"
    )


def roster_from_document(document: Mapping[str, Any]) -> dict[str, RosterEntry]:
    """Read a committed fixture document back into roster entries."""
    return {
        model: RosterEntry(
            efforts=tuple(entry["efforts"]), tiers=tuple(entry["tiers"])
        )
        for model, entry in document["roster"].items()
    }


@dataclass(frozen=True)
class RosterDrift:
    """How the committed roster differs from what the pinned harness reports.

    The three kinds are kept apart because they mean different things: a model
    the harness no longer offers is a **removal** the kit must stop routing to,
    a new model is an **arrival** the kit silently warns about on every startup
    until it lands, and a changed capability set is the quiet one — the kit keeps
    working while sending (or refusing) an effort the harness disagrees about.
    """

    added: list[str]
    removed: list[str]
    changed: list[tuple[str, RosterEntry, RosterEntry]]

    @property
    def drifted(self) -> bool:
        """True when the committed roster no longer describes the harness."""
        return bool(self.added or self.removed or self.changed)


def classify(
    *,
    committed: Mapping[str, RosterEntry],
    live: Mapping[str, RosterEntry],
) -> RosterDrift:
    """Diff a committed roster against a live one **without** writing anything."""
    added = [model for model in live if model not in committed]
    removed = [model for model in committed if model not in live]
    changed = [
        (model, entry, live[model])
        for model, entry in committed.items()
        if model in live and live[model] != entry
    ]
    return RosterDrift(added=added, removed=removed, changed=changed)


def _render_capability(entry: RosterEntry) -> str:
    """One entry as an operator reads it: ``efforts=[...] tiers=[...]``.

    An empty effort list renders as ``[]`` rather than a dash: "accepts no
    reasoning effort" is the load-bearing value that makes the harness reject a
    session outright, not missing data.
    """
    return (
        f"efforts={json.dumps(list(entry.efforts))} "
        f"tiers={json.dumps(list(entry.tiers))}"
    )


def describe_drift(
    drift: RosterDrift,
    *,
    committed_cli_version: str,
    live_cli_version: str,
) -> str:
    """A drift report naming the ids, the capability sets, and both harnesses.

    A stamp that no longer names the harness in front of it is itself drift,
    even when every entry survived: the fixture's job is to say *which* CLI
    accepted these values.
    """
    if not drift.drifted:
        if committed_cli_version == live_cli_version:
            return (
                f"model roster is in sync with Copilot CLI {live_cli_version} "
                f"(fixture stamped {committed_cli_version})."
            )
        return (
            f"model roster entries are unchanged, but the fixture is stamped "
            f"Copilot CLI {committed_cli_version} while the harness reports "
            f"{live_cli_version}.\nregenerate and commit with:\n  " + SYNC_COMMAND
        )
    if committed_cli_version == live_cli_version:
        header = (
            f"model roster drift against Copilot CLI {live_cli_version}, the "
            f"version the fixture is stamped with:"
        )
    else:
        header = (
            f"model roster drift: the committed fixture was generated against "
            f"Copilot CLI {committed_cli_version}; the harness now reports "
            f"{live_cli_version}."
        )
    lines = [header]
    for model in drift.added:
        lines.append(f"  + {model} (in the harness, missing from the fixture)")
    for model in drift.removed:
        lines.append(f"  - {model} (in the fixture, not offered by the harness)")
    for model, committed_entry, live_entry in drift.changed:
        lines.append(
            f"  ~ {model}: fixture {_render_capability(committed_entry)} "
            f"-> harness {_render_capability(live_entry)}"
        )
    lines.append("regenerate and commit with:\n  " + SYNC_COMMAND)
    return "\n".join(lines)


#: Sync ``() -> Sequence[ModelInfo]`` catalog fetch, injected so the command's
#: check and write paths are testable without a backend (mirroring
#: :data:`git_loopy.interactive.picker.ModelFetcher`).
CatalogFetcher = Callable[[], Sequence[Any]]


def _pinned_cli_version() -> str:
    """The Copilot CLI version the pinned SDK spawns (**not** the one on PATH)."""
    from copilot._cli_version import CLI_VERSION

    return str(CLI_VERSION)


def fetch_live_models() -> Sequence[Any]:
    """List models through a throwaway client, exactly as the picker does.

    The client is entered as an async context manager so ``start()`` / ``stop()``
    bracket the single ``list_models()`` call, and the spawned CLI is the SDK's
    own pinned binary — which is the whole authority question ADR-0019 settled.
    """

    async def _list() -> Sequence[Any]:
        from copilot import CopilotClient

        client = CopilotClient()
        async with client:
            return await client.list_models()

    return asyncio.run(_list())


def main(
    argv: list[str] | None = None,
    *,
    fetch: CatalogFetcher | None = None,
    fixture: Path = ROSTER_FIXTURE,
    cli_version: str | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate conformance/model-roster.json from the model catalog "
            "the pinned Copilot CLI reports, stamped with its version."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Report drift and exit non-zero without writing. Needs Copilot "
            "authentication, so this is a maintainer's command and is "
            "deliberately not run in CI (see ADR-0019)."
        ),
    )
    args = parser.parse_args(argv)

    environment = os.environ if env is None else env
    relocated = environment.get("COPILOT_CLI_PATH", "").strip()
    if relocated:
        # The stamp comes from the SDK's own constant, so a relocated harness
        # would be recorded under the pinned CLI's version -- the operator's
        # binary being mistaken for the kit's is the exact confusion that
        # produced the drift this generator ends (ADR-0019).
        print(
            f"error: COPILOT_CLI_PATH relocates the harness to {relocated!r}, "
            "whose catalog cannot be stamped as the pinned CLI; unset it and "
            "re-run",
            file=sys.stderr,
        )
        return 2

    fetcher = fetch_live_models if fetch is None else fetch
    live_cli_version = _pinned_cli_version() if cli_version is None else cli_version
    live = roster_from_models(fetcher())
    if not live:
        # The picker treats an empty ``list_models()`` as a failed fetch rather
        # than as a catalog of no models; writing it here would commit a roster
        # that gates every model to unknown.
        print(
            "error: the harness reported an empty model catalog; refusing to "
            "write (check authentication and retry)",
            file=sys.stderr,
        )
        return 2

    if args.check:
        if not fixture.is_file():
            print(f"error: no committed roster at {fixture}", file=sys.stderr)
            return 2
        document = json.loads(fixture.read_text(encoding="utf-8"))
        committed_cli_version = str(document.get("cli_version", "unstamped"))
        drift = classify(committed=roster_from_document(document), live=live)
        report = describe_drift(
            drift,
            committed_cli_version=committed_cli_version,
            live_cli_version=live_cli_version,
        )
        # A stale stamp is drift even when every entry survived the bump: the
        # offline CI guard compares the stamp against the pinned SDK, so
        # reporting "in sync" here would send a maintainer into a red build.
        if drift.drifted or committed_cli_version != live_cli_version:
            print(report, file=sys.stderr)
            return 1
        print(report)
        return 0

    fixture.write_text(
        render_document(live, cli_version=live_cli_version), encoding="utf-8"
    )
    print(
        f"Wrote {len(live)} models to {fixture} "
        f"(Copilot CLI {live_cli_version}); review and commit."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
