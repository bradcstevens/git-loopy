"""``git_loopy.roster_cache`` — the roster the *operator's own* harness offers.

[ADR-0019](../../../docs/adr/0019-roster-derived-from-the-pinned-harness.md)
established that the live ``models.list`` of the CLI the SDK spawns is the only
authority on which models exist, and that the committed fixture is an offline
fallback stamped with the version it was captured against. This module supplies
the missing half of that decision: a place for the *observed* answer to live
between invocations.

**Why a cache rather than a live read at the point of use.** The surfaces that
ask "is this model real?" are synchronous, and two of them — argument parsing and
``git-loopy config`` — run before any harness is contacted and must keep working
with no network, no credentials, and no spawned CLI. Making them live would put
a round trip behind ``--help``. So the kit records what it observed the last time
it legitimately talked to the harness, and the advisory surfaces read that.

**Why the live listing wins.** A Run should reflect the actual models the current
operator's harness exposes on this machine. If a fresh ``models.list`` succeeds,
that answer becomes the supported set for the advisory surfaces; the built-in
fixture remains only a safe fallback when the live listing is unavailable and no
valid cached observation exists.

The cache is advisory in the strict sense: it feeds typo-catching warnings, never
a refusal. ``git_loopy.static_route`` continues to refuse an unlisted model from
the *fresh* listing it reads at that moment, because an eligibility decision may
not be made from a remembered answer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from git_loopy.config import MODEL_REASONING_EFFORTS
from git_loopy.settings import global_dir

__all__ = [
    "SCHEMA_VERSION",
    "roster_cache_path",
    "record_observed_roster",
    "observed_models",
    "supported_models",
]

#: Bumped only when the on-disk shape changes. A document carrying anything else
#: is ignored rather than migrated: this is a cache of something re-observable at
#: the next harness read, so discarding it costs nothing and guessing costs
#: correctness.
SCHEMA_VERSION = 1


def roster_cache_path(env: Mapping[str, str] | None = None) -> Path:
    """Where the observed roster is remembered, beside the operator's Config."""
    return global_dir(os.environ if env is None else env) / "model-roster.json"


def record_observed_roster(
    capabilities: Any,
    *,
    env: Mapping[str, str] | None = None,
    cli_version: str | None = None,
) -> None:
    """Remember the models one live harness listing offered.

    Best-effort by construction. This is called from the capability refresh that
    a Run's preflight and ``git-loopy doctor`` both depend on, and a cache is
    never worth failing that read for: an unwritable config directory, a
    read-only home, or a listing shaped differently than expected all leave the
    previous answer in place and are silently tolerated.
    """
    try:
        models = getattr(capabilities, "models", None)
        if models is None:
            return
        document: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "models": sorted(str(model) for model in models),
        }
        if cli_version is not None:
            document["cli_version"] = cli_version
        path = roster_cache_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written through a sibling temporary file so a concurrent reader never
        # observes a half-written document, and an interrupted write cannot
        # leave an unparseable cache behind.
        scratch = path.with_name(f".{path.name}.tmp")
        scratch.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        os.replace(scratch, path)
    except Exception:
        # Deliberately total. The caller is a capability read whose contract is
        # that every failure answers ``None`` rather than raising, because it
        # runs at Run preflight before a single Event exists — a traceback there
        # is not a refusal an operator can read. Resolving the path alone can
        # raise beyond the obvious OSError: with neither XDG_CONFIG_HOME nor
        # HOME set, `Path.home()` raises RuntimeError, which is reachable on a
        # Windows host carrying none of the variables it consults.
        return


def observed_models(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """The models the last observed harness listing offered, or an empty set.

    Every failure — absent, unreadable, malformed, or written by a future schema
    — answers an empty set. :func:`supported_models` retains the internal
    distinction between that failure and a successful empty listing so only the
    former falls back to the built-in roster.
    """
    observed = _read_observed_models(env)
    return frozenset() if observed is None else observed


def _read_observed_models(
    env: Mapping[str, str] | None = None,
) -> frozenset[str] | None:
    """Read a valid observation, distinguishing an empty roster from no cache."""
    try:
        document = json.loads(roster_cache_path(env).read_text(encoding="utf-8"))
    except Exception:
        # Total for the same reason the write is: resolving the path can raise
        # beyond OSError (RuntimeError from `Path.home()` with no HOME), and an
        # unreadable advisory cache must degrade to the built-in roster rather
        # than take down whatever asked whether a model was known.
        return None
    if not isinstance(document, dict):
        return None
    if document.get("schema_version") != SCHEMA_VERSION:
        return None
    models = document.get("models")
    if not isinstance(models, list):
        return None
    return frozenset(model for model in models if isinstance(model, str) and model)


def supported_models(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """The model ids this kit treats as known, on this operator's harness.

    When a fresh live listing was observed, it is the authoritative roster for
    this operator. The built-in fixture remains as the fallback only when no live
    observation exists yet, so that offline and unauthenticated invocations still
    behave predictably without claiming support for models the current harness is
    not actually offering.
    """
    observed = _read_observed_models(env)
    if observed is not None:
        return observed
    return frozenset(MODEL_REASONING_EFFORTS)
