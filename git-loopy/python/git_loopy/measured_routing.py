"""``git_loopy.measured_routing`` — the Measured routing artifact (#361, ADR-0028).

**Measured routing** is a precedence tier, not a write into ``config.toml``. The
chain ADR-0006 shipped gains one rung:

    CLI flag > env var > project Config > global Config > **measured** > built-in default

so a routing table git-loopy did not author can supply a **Routed pair** — but
**only where the operator is silent**. A hand-written ``[routing]`` entry wins
forever, with no override flag and no special case, because that is the chain
that already shipped.

The tier reads **one committed artifact**, ``git-loopy/routing.measured.toml``,
beside the project ``config.toml`` and in the same TOML dialect. Only routing
participates: no other Config key is machine-written. Deleting the file is how an
operator opts out — routing falls straight back to Config and the built-in
defaults, with nothing else to undo.

Design notes:

* **The evidence lives in the same file as the table**, inline and first-class,
  so the table and its justification cannot disagree (ADR-0028). Every field is
  machine-checkable and there is deliberately **no** ``rationale`` / ``summary``
  / ``conclusion`` key — the moment one exists, something writes an opinion into
  it. :func:`load_measured_routing` rejects unknown keys rather than dropping
  them, which is what keeps that guarantee enforceable rather than aspirational.
* **Current state only.** Git is the ledger: ``git log -p`` is every past
  **Calibration** in order, ``git blame`` names which one set a **Task type**'s
  model, and ``git revert`` undoes a bad one.
* **Four states, each honest about itself.** ``measured`` carries a winning
  pair; ``incomplete`` carries where the search stopped and **no pair at all**,
  because an unfinished search publishes no winner; ``demoted`` carries the pair
  that failed and the **Strike** count that removed it, so a later Calibration
  knows it was tried in production; ``provisional`` carries a pair that is **in
  force and was never measured** (#376, ADR-0030) — what **Demotion** produces
  when it steps *up* the price staircase, since cheapest-first stops at the first
  pass and so nothing above the winner was ever trialled. ``measured`` and
  ``provisional`` supply a Routed pair; only ``measured`` is evidence.
* **I/O confined to the load/write functions**, mirroring
  :mod:`git_loopy.settings`. Everything else is pure.
* **Evidence never pools across repositories.** The artifact is per-repository
  by construction: it is resolved from the repo root and committed to that repo.

Nothing in this module runs a Calibration or spends an **AI Credit**. Hand-place
an artifact and routing honours it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, cast

from git_loopy.config import TASK_TYPE_KEYS, TaskTypeError, validate_task_type_key
from git_loopy.settings import SettingsError

__all__ = [
    "MEASURED_ROUTING_FILENAME",
    "SCHEMA_VERSION",
    "MeasuredStatus",
    "ProvisionalReason",
    "Provenance",
    "Rung",
    "ProvingTask",
    "MeasuredEntry",
    "MeasuredRouting",
    "measured_routing_path",
    "load_measured_routing",
    "dump_measured_routing",
    "write_measured_routing",
]

#: The artifact's filename, inside the project scope dir ``<repo>/git-loopy/``.
MEASURED_ROUTING_FILENAME = "routing.measured.toml"

#: The artifact schema this build reads and writes. Enforced, not advisory: a
#: file stamped with anything else is rejected by name rather than
#: half-understood.
#:
#: The fourth record state (``provisional``, #376) deliberately **does not** bump
#: this. The reader accepts exactly one version, so a bump would make this build
#: reject every artifact already written against schema 1 — a real regression, in
#: exchange for nothing: an older build meeting a ``provisional`` record already
#: fails at load *naming the status it does not know*, which is the diagnosis the
#: version would have bought. The states are additive and the loader's
#: unknown-key and unknown-status rejections are what keep the addition honest.
SCHEMA_VERSION = 1

#: The project scope directory the artifact shares with ``config.toml``.
_APP_DIR = "git-loopy"

#: The scope name every :exc:`SettingsError` from this module carries, so a
#: failure reads the same way a malformed ``config.toml`` does.
_SCOPE = "measured routing"

#: The artifact's top-level keys. Anything else is rejected, not dropped.
_TOP_LEVEL_KEYS = frozenset({"schema_version", "provenance", "routing"})

#: ``[provenance]``'s required keys — all four, none of them free text.
_PROVENANCE_KEYS = frozenset(
    {"cli_version", "calibrated_at", "candidate_count", "gate_loops"}
)

#: ``[provenance]``'s optional keys: the **pinned classifier pair** the
#: **Calibration** stratified its **Proving set** under (ADR-0028's amendment,
#: ADR-0029). Optional rather than required, because a required key would reject
#: every schema-1 artifact already written for a fact those files could not have
#: carried — and an unstamped artifact is not a *drifted* one, it is one that
#: cannot answer the question, which reads as silence rather than as a change.
#:
#: The effort is spelled the way the rungs spell it: TOML has no ``None``, so a
#: reasoning-incapable model stamps ``""``.
_PROVENANCE_OPTIONAL_KEYS = frozenset({"classifier_model", "classifier_effort"})

#: ``[[routing.<key>.rung]]``'s keys.
_RUNG_KEYS = frozenset({"model", "effort", "passed", "total", "credits"})

#: ``[[routing.<key>.proving_task]]``'s keys.
_PROVING_TASK_KEYS = frozenset({"issue", "base_commit", "oracle_commit"})

#: The evidence arrays every record state may carry.
_EVIDENCE_KEYS = frozenset({"rung", "proving_task"})


class MeasuredStatus(Enum):
    """What a **Task type**'s record in the artifact actually says.

    :attr:`MEASURED` and :attr:`PROVISIONAL` supply a **Routed pair**; only the
    first of them is *evidence*. The other states exist so a search that did not
    finish, a pair production disagreed with, and a pair in force that nobody
    measured are each representable *as themselves* rather than as an absence — or,
    worse, as a measurement.
    """

    #: A completed **Calibration** with a winning pair.
    MEASURED = "measured"
    #: A search that hit its **AI Credit** ceiling or was interrupted. Carries
    #: where it stopped and no pair at all — a stopped search must look stopped.
    INCOMPLETE = "incomplete"
    #: A measured pair removed after consecutive **Strikes** on real work. The
    #: pair is cleared; which pair failed, and after how many Strikes, is kept.
    DEMOTED = "demoted"
    #: A pair that is **in force and was never measured** (#376, ADR-0030): what
    #: **Demotion** installs when it steps up the price staircase into a rung
    #: nobody trialled. Carries the pair now in force, the pair it replaced and
    #: why, and — deliberately — no evidence at all.
    PROVISIONAL = "provisional"


class ProvisionalReason(Enum):
    """Why a :attr:`~MeasuredStatus.PROVISIONAL` pair is in force.

    A **closed vocabulary**, not a sentence. ADR-0028 forbids a free-text field
    anywhere in the artifact — the moment one exists something writes an opinion
    into it — so "why" is a value the loader can check rather than prose a reader
    has to trust.
    """

    #: **Demotion** (ADR-0030) replaced a measured pair that stopped making
    #: progress on real work with the next pair up the price staircase.
    DEMOTION = "demotion"


@dataclass(frozen=True)
class Provenance:
    """What the **Calibration** that wrote this artifact ran against.

    ADR-0028's answer to the standalone model-availability log: the provenance
    sits on the thing it justifies, so *"has the roster changed?"* is live roster
    versus the roster stamped here — one source of truth, no second file. This is
    ADR-0019's finding (*a persisted roster whose real defect is that it names no
    version at all*) applied one level up.
    """

    cli_version: str
    calibrated_at: str
    candidate_count: int
    gate_loops: tuple[str, ...]
    #: The **Task-type classifier** pair this Calibration's **Proving set** was
    #: stratified under, or ``None`` on an artifact written before the pin was
    #: stamped. The classifier runs on the cheapest rung of the live roster
    #: (ADR-0029), so it moves when the roster moves — and a Proving set
    #: stratified by one pin, measured against work labelled by another, is
    #: comparing across a taxonomy that shifted underneath it. Stamping the pin
    #: here is what gives that change something to be a change *from*.
    classifier_model: str | None = None
    #: The pinned pair's reasoning effort, ``""`` for a reasoning-incapable
    #: model. Meaningless without :attr:`classifier_model`, and refused without
    #: it — half a pair is not a pair, and would compare as one.
    classifier_effort: str | None = None

    def __post_init__(self) -> None:
        """Refuse half a pinned pair, in either direction.

        ``model`` without ``effort`` is not a weaker stamp, it is an ambiguous
        one: :func:`~git_loopy.roster_drift.compare_classifier_pin` normalises a
        missing effort to the *empty* effort, so a half-stamp would compare equal
        to a reasoning-incapable pin and unequal to every other — silently
        answering a question nobody asked. Both or neither.
        """
        if (self.classifier_model is None) != (self.classifier_effort is None):
            raise ValueError(
                "a pinned classifier pair stamps both 'classifier_model' and "
                "'classifier_effort', or neither; half a pair is not a pair"
            )


@dataclass(frozen=True)
class Rung:
    """One rung of the price staircase the search walked, and how it scored."""

    model: str
    effort: str
    passed: int
    total: int
    credits: float


@dataclass(frozen=True)
class ProvingTask:
    """A **Proving task** the search measured against, pinned so it can be replayed.

    Issue number, base commit and oracle commit together identify it exactly, so
    a later Calibration re-measures the same work rather than something adjacent.
    """

    issue: int
    base_commit: str
    oracle_commit: str


@dataclass(frozen=True)
class MeasuredEntry:
    """One **Task type**'s record: its state, its pair (if any) and its evidence."""

    status: MeasuredStatus
    model: str | None = None
    effort: str | None = None
    trials_passed: int | None = None
    trials_total: int | None = None
    rungs_walked: int | None = None
    credits: float | None = None
    wall_clock_seconds: int | None = None
    stopped_at_rung: int | None = None
    rungs_available: int | None = None
    demoted_model: str | None = None
    demoted_effort: str | None = None
    demoted_after_strikes: int | None = None
    replaced_model: str | None = None
    replaced_effort: str | None = None
    reason: ProvisionalReason | None = None
    rungs: tuple[Rung, ...] = ()
    proving_tasks: tuple[ProvingTask, ...] = ()

    def __post_init__(self) -> None:
        """Hold the record to its own state's field set, in memory as on disk.

        The same table :func:`load_measured_routing` validates a parsed record
        against, applied at construction — so a record cannot be *built* in a
        shape the artifact could not hold. Without this the writer would silently
        drop a field it was handed (it emits only the state's own keys) or emit a
        ``None`` its own reader rejects, and the two halves of the seam would
        disagree about what the record says.
        """
        required = _REQUIRED_KEYS[self.status] - {"status"}
        forbidden = _STATE_SCALARS - required
        missing = sorted(name for name in required if getattr(self, name) is None)
        if missing:
            raise ValueError(f"a {self.status.value!r} record requires {missing}")
        present = sorted(name for name in forbidden if getattr(self, name) is not None)
        if present:
            raise ValueError(f"a {self.status.value!r} record may not carry {present}")
        if self.status is MeasuredStatus.MEASURED:
            self._check_it_stands_on_its_own()
        if self.status is MeasuredStatus.PROVISIONAL:
            self._check_it_looks_unmeasured()

    def _check_it_stands_on_its_own(self) -> None:
        """A winner must carry the evidence that chose it, and agree with it.

        ADR-0028 accepts that the raw per-**Trial** log stays local and
        disposable, on the condition that *"the distillate must stand on its
        own"*. A ``measured`` record with no rungs and no **Proving tasks** is not
        a distillate but an assertion — which is the thing this tier exists to
        replace — and one claiming a winner on a partial tally contradicts
        ADR-0027's unanimous promotion rule in the same breath as invoking it.
        """
        if not self.rungs:
            raise ValueError(
                "a 'measured' record must carry the rung(s) it walked; a table "
                "with no justification beside it is the drift ADR-0028 forbids"
            )
        if not self.proving_tasks:
            raise ValueError(
                "a 'measured' record must name the proving_task(s) it measured, "
                "so the same work is re-measured later rather than something adjacent"
            )
        if self.trials_total is not None and self.trials_total < 1:
            raise ValueError("a 'measured' record needs at least one Trial")
        if self.trials_passed != self.trials_total:
            raise ValueError(
                f"promotion is unanimous (ADR-0027), so trials_passed "
                f"({self.trials_passed}) must equal trials_total "
                f"({self.trials_total}); a partial tally names no winner"
            )
        if self.rungs_walked is not None and self.rungs_walked < 1:
            raise ValueError("a 'measured' record walked at least one rung")

    def _check_it_looks_unmeasured(self) -> None:
        """A provisional row must be readable at a glance as *not* evidence.

        Three things are enforced beyond the state's own key set. ``reason`` must
        be the closed :class:`ProvisionalReason` vocabulary and not merely
        *present*, because a bare string satisfies "not ``None``" and would be
        written straight through — the one provisional key that could have been
        prose, holding ADR-0028's "no free text" only on the way in. A
        replacement that names the pair it replaced replaced nothing, so the two
        pairs must differ. And a provisional pair has been through no **Trial** at
        all — the rungs of the **Calibration** that chose the pair it *replaced*
        sitting beside it would be another pair's evidence read as its own, which
        is the exact confusion this state exists to prevent (ADR-0030).
        """
        if not isinstance(self.reason, ProvisionalReason):
            raise ValueError(
                f"a 'provisional' record's reason is a ProvisionalReason, not "
                f"{self.reason!r}; permitted: "
                f"{sorted(member.value for member in ProvisionalReason)}"
            )
        if (self.model, self.effort) == (self.replaced_model, self.replaced_effort):
            raise ValueError(
                f"a 'provisional' record replaces a pair with a different one; "
                f"{self.model} @ {self.effort} replaced itself"
            )
        if self.rungs or self.proving_tasks:
            raise ValueError(
                "a 'provisional' record carries no rung and no proving_task: "
                "nothing measured this pair, and evidence beside it would read "
                "as though something had"
            )

    @property
    def routed_pair(self) -> tuple[str, str] | None:
        """The ``(model, effort)`` this record supplies, or ``None``.

        A :attr:`~MeasuredStatus.MEASURED` record supplies the pair a
        **Calibration** measured; a :attr:`~MeasuredStatus.PROVISIONAL` one
        supplies the pair **Demotion** put in force without measuring. Both are
        genuinely the Routed pair for their **Task type**, which is why the tier
        contributes both — what separates them is the status, not the routing. An
        ``incomplete`` search publishes no winner and a ``demoted`` entry has had
        its pair cleared, so both fall through to the next tier. The record's own
        invariant guarantees a pair-carrying record has both halves of the pair.
        """
        if self.status not in _PAIR_SUPPLYING_STATES:
            return None
        return (cast("str", self.model), cast("str", self.effort))


@dataclass(frozen=True)
class MeasuredRouting:
    """The parsed artifact: its provenance and its per-**Task type** records."""

    entries: Mapping[str, MeasuredEntry] = field(default_factory=dict)
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        """Reject records outside the task-type taxonomy before they are written."""
        for key in self.entries:
            validate_task_type_key(key)

    @property
    def routing(self) -> dict[str, tuple[str, str]]:
        """The routing map this tier contributes — measured and provisional records."""
        return {
            key: pair
            for key, entry in self.entries.items()
            if (pair := entry.routed_pair) is not None
        }

    @property
    def provisional_keys(self) -> frozenset[str]:
        """Which keys of :attr:`routing` are in force without having been measured.

        Read beside the map rather than folded into it: the pair is genuinely
        what routes, so it belongs in :attr:`routing` — but a reporting surface
        that printed it as ``measured`` would be the failure this state exists to
        prevent, and this is what lets the tier be named honestly (#376).
        """
        return frozenset(
            key
            for key, entry in self.entries.items()
            if entry.status is MeasuredStatus.PROVISIONAL
        )


def measured_routing_path(repo_root: Path) -> Path:
    """Resolve the artifact path: ``<repo-root>/git-loopy/routing.measured.toml``.

    The same project scope dir :func:`git_loopy.settings.project_config_path`
    resolves, because the tier belongs to the repository the work happens in and
    evidence never pools across repositories.
    """
    return repo_root / _APP_DIR / MEASURED_ROUTING_FILENAME


def load_measured_routing(path: Path | None) -> MeasuredRouting:
    """Parse the Measured routing artifact at ``path``.

    An **absent artifact is the ordinary case** and warns about nothing — it is
    how an operator opts out, and how every repository that has never calibrated
    looks. Absence means a *missing file*, and nothing else: a file that exists
    but is empty is malformed, because every artifact must state the schema it
    was written against. Malformed TOML, an unknown schema version, an unknown
    key, a wrong-typed value or a record its own evidence contradicts all raise
    :exc:`~git_loopy.settings.SettingsError` naming the scope, rather than being
    silently ignored: a machine-written file that is quietly half-read is worse
    than one that fails at load.
    """
    if path is None:
        return MeasuredRouting()
    try:
        with open(path, "rb") as handle:
            table = tomllib.load(handle)
    except FileNotFoundError:
        return MeasuredRouting()
    except IsADirectoryError:  # pragma: no cover - defensive
        return MeasuredRouting()
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(
            f"{_SCOPE} artifact {path} is not valid TOML: {exc}"
        ) from exc
    return _parse(table, path)


def _parse(table: Mapping[str, object], path: Path) -> MeasuredRouting:
    """Validate and convert one parsed artifact table."""
    _reject_unknown(table, _TOP_LEVEL_KEYS, where="artifact", path=path)
    version = table.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise _error(path, "'schema_version' must be an integer")
    if version != SCHEMA_VERSION:
        raise _error(
            path,
            f"unsupported schema_version {version} (this build reads "
            f"{SCHEMA_VERSION})",
        )
    provenance = _parse_provenance(table.get("provenance"), path)
    routing = table.get("routing", {})
    if not isinstance(routing, dict):
        raise _error(path, "'routing' must be a table of per-task-type records")
    entries: dict[str, MeasuredEntry] = {}
    for key, entry in cast("Mapping[str, object]", routing).items():
        try:
            validate_task_type_key(key)
        except TaskTypeError:
            raise _error(
                path,
                f"routing key {key!r} is unsupported; permitted keys: "
                f"{', '.join(TASK_TYPE_KEYS)}",
            ) from None
        entries[key] = _parse_entry(key, entry, path)
    if entries and provenance is None:
        raise _error(
            path,
            "an artifact carrying records must carry its 'provenance' — the "
            "record and what justifies it live in one file, or they drift",
        )
    return MeasuredRouting(entries=entries, provenance=provenance)


def _parse_provenance(raw: object, path: Path) -> Provenance | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _error(path, "'provenance' must be a table")
    entry = cast("Mapping[str, object]", raw)
    _reject_unknown(
        entry,
        _PROVENANCE_KEYS | _PROVENANCE_OPTIONAL_KEYS,
        where="provenance",
        path=path,
    )
    _require(entry, _PROVENANCE_KEYS, where="provenance", path=path)
    stamped = _PROVENANCE_OPTIONAL_KEYS & set(entry)
    if stamped and stamped != _PROVENANCE_OPTIONAL_KEYS:
        raise _error(
            path,
            f"provenance carries {sorted(stamped)} without "
            f"{sorted(_PROVENANCE_OPTIONAL_KEYS - stamped)}; a pinned classifier "
            f"pair stamps both keys or neither",
        )
    return Provenance(
        cli_version=_str(entry, "cli_version", "provenance", path),
        calibrated_at=_str(entry, "calibrated_at", "provenance", path),
        candidate_count=_int(entry, "candidate_count", "provenance", path),
        gate_loops=_str_tuple(entry, "gate_loops", "provenance", path),
        classifier_model=(
            _str(entry, "classifier_model", "provenance", path)
            if "classifier_model" in entry
            else None
        ),
        classifier_effort=(
            _str(entry, "classifier_effort", "provenance", path)
            if "classifier_effort" in entry
            else None
        ),
    )


def _parse_entry(key: str, raw: object, path: Path) -> MeasuredEntry:
    where = f"routing.{key}"
    if not isinstance(raw, dict):
        raise _error(path, f"{where!r} must be a table")
    entry = cast("Mapping[str, object]", raw)
    status_raw = entry.get("status")
    if not isinstance(status_raw, str):
        raise _error(path, f"{where!r} must carry a string 'status'")
    try:
        status = MeasuredStatus(status_raw)
    except ValueError:
        raise _error(
            path,
            f"{where}.status is {status_raw!r}; expected one of "
            f"{sorted(member.value for member in MeasuredStatus)}",
        ) from None
    required = _REQUIRED_KEYS[status]
    evidence = _EVIDENCE_KEYS if status in _EVIDENCE_STATES else frozenset()
    _reject_unknown(entry, required | evidence, where=where, path=path)
    _require(entry, required, where=where, path=path)
    fields: dict[str, object] = {
        "status": status,
        "rungs": _parse_rungs(entry.get("rung"), where, path),
        "proving_tasks": _parse_proving_tasks(entry.get("proving_task"), where, path),
    }
    for name in required - {"status"}:
        fields[name] = _SCALAR_READERS[name](entry, name, where, path)
    try:
        return MeasuredEntry(**fields)  # type: ignore[arg-type]
    except ValueError as exc:
        # The record's own invariant is the single statement of what each state
        # may say; on load it is reported the way every other artifact failure is.
        raise _error(path, f"{where}: {exc}") from exc


def _parse_rungs(raw: object, where: str, path: Path) -> tuple[Rung, ...]:
    return tuple(
        Rung(
            model=_str(item, "model", f"{where}.rung", path),
            effort=_str(item, "effort", f"{where}.rung", path),
            passed=_int(item, "passed", f"{where}.rung", path),
            total=_int(item, "total", f"{where}.rung", path),
            credits=_float(item, "credits", f"{where}.rung", path),
        )
        for item in _array_of_tables(raw, _RUNG_KEYS, f"{where}.rung", path)
    )


def _parse_proving_tasks(
    raw: object, where: str, path: Path
) -> tuple[ProvingTask, ...]:
    return tuple(
        ProvingTask(
            issue=_int(item, "issue", f"{where}.proving_task", path),
            base_commit=_str(item, "base_commit", f"{where}.proving_task", path),
            oracle_commit=_str(item, "oracle_commit", f"{where}.proving_task", path),
        )
        for item in _array_of_tables(
            raw, _PROVING_TASK_KEYS, f"{where}.proving_task", path
        )
    )


def _array_of_tables(
    raw: object, keys: frozenset[str], where: str, path: Path
) -> list[Mapping[str, object]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _error(path, f"{where!r} must be an array of tables")
    items: list[Mapping[str, object]] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            raise _error(path, f"{where!r} must be an array of tables")
        entry = cast("Mapping[str, object]", item)
        _reject_unknown(entry, keys, where=where, path=path)
        _require(entry, keys, where=where, path=path)
        items.append(entry)
    return items


# ---------------------------------------------------------------------------
# Validation helpers. Every failure names the artifact and the offending key, so
# a machine-written file fails at load rather than surfacing deep in a Run.
# ---------------------------------------------------------------------------


def _error(path: Path, message: str) -> SettingsError:
    return SettingsError(f"{_SCOPE} artifact {path}: {message}")


def _reject_unknown(
    table: Mapping[str, object], allowed: frozenset[str], *, where: str, path: Path
) -> None:
    """Reject keys outside ``allowed`` — never drop them.

    This is what makes ADR-0028's "no free-text field anywhere" enforceable: a
    ``rationale`` key cannot be written into the artifact and quietly survive.
    """
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise _error(
            path,
            f"{where} carries unknown key(s) {unknown}; allowed: {sorted(allowed)}",
        )


def _require(
    table: Mapping[str, object], required: frozenset[str], *, where: str, path: Path
) -> None:
    missing = sorted(required - set(table))
    if missing:
        raise _error(path, f"{where} is missing required key(s) {missing}")


def _str(table: Mapping[str, object], key: str, where: str, path: Path) -> str:
    value = table.get(key)
    if not isinstance(value, str):
        raise _error(path, f"{where}.{key} must be a string, got {value!r}")
    return value


def _int(table: Mapping[str, object], key: str, where: str, path: Path) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _error(path, f"{where}.{key} must be an integer, got {value!r}")
    return value


def _float(table: Mapping[str, object], key: str, where: str, path: Path) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(path, f"{where}.{key} must be a number, got {value!r}")
    return float(value)


def _str_tuple(
    table: Mapping[str, object], key: str, where: str, path: Path
) -> tuple[str, ...]:
    value = table.get(key)
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in cast("list[object]", value)
    ):
        raise _error(path, f"{where}.{key} must be a list of strings, got {value!r}")
    return tuple(cast("list[str]", value))


def _reason(
    table: Mapping[str, object], key: str, where: str, path: Path
) -> ProvisionalReason:
    """Read one ``reason`` against its closed vocabulary.

    Rejecting an unrecognised value by name is what keeps ``reason`` a vocabulary
    rather than the free-text field ADR-0028 forbids: prose would parse.
    """
    value = table.get(key)
    if not isinstance(value, str):
        raise _error(path, f"{where}.{key} must be a string, got {value!r}")
    try:
        return ProvisionalReason(value)
    except ValueError:
        raise _error(
            path,
            f"{where}.{key} is {value!r}; expected one of "
            f"{sorted(member.value for member in ProvisionalReason)}",
        ) from None


#: Per-state scalar readers, so a key's type is declared once.
_SCALAR_READERS: Mapping[str, object] = {
    "model": _str,
    "effort": _str,
    "trials_passed": _int,
    "trials_total": _int,
    "rungs_walked": _int,
    "credits": _float,
    "wall_clock_seconds": _int,
    "stopped_at_rung": _int,
    "rungs_available": _int,
    "demoted_model": _str,
    "demoted_effort": _str,
    "demoted_after_strikes": _int,
    "replaced_model": _str,
    "replaced_effort": _str,
    "reason": _reason,
}

#: Every state-governed scalar field. A record may carry exactly the ones its own
#: state requires, and none of the others.
_STATE_SCALARS = frozenset(_SCALAR_READERS)

#: The keys each state **requires**, and — with :data:`_EVIDENCE_KEYS` — the only
#: keys it permits. A record is validated against its own state's row, which is
#: how ``incomplete`` is stopped from carrying a winner it does not have.
_REQUIRED_KEYS: Mapping[MeasuredStatus, frozenset[str]] = {
    MeasuredStatus.MEASURED: frozenset(
        {
            "status",
            "model",
            "effort",
            "trials_passed",
            "trials_total",
            "rungs_walked",
            "credits",
            "wall_clock_seconds",
        }
    ),
    MeasuredStatus.INCOMPLETE: frozenset(
        {
            "status",
            "stopped_at_rung",
            "rungs_available",
            "credits",
            "wall_clock_seconds",
        }
    ),
    MeasuredStatus.DEMOTED: frozenset(
        {
            "status",
            "demoted_model",
            "demoted_effort",
            "demoted_after_strikes",
        }
    ),
    MeasuredStatus.PROVISIONAL: frozenset(
        {
            "status",
            "model",
            "effort",
            "replaced_model",
            "replaced_effort",
            "reason",
        }
    ),
}

#: The states that supply a **Routed pair** to the precedence chain. ``measured``
#: because a Calibration chose it; ``provisional`` because **Demotion** put it in
#: force. What separates them is the status, not whether they route.
_PAIR_SUPPLYING_STATES: frozenset[MeasuredStatus] = frozenset(
    {MeasuredStatus.MEASURED, MeasuredStatus.PROVISIONAL}
)

#: The states whose records may carry the evidence arrays at all. ``provisional``
#: is excluded by construction: nothing trialled the pair, so any rung beside it
#: would belong to the pair it replaced (ADR-0030).
_EVIDENCE_STATES: frozenset[MeasuredStatus] = frozenset(
    {MeasuredStatus.MEASURED, MeasuredStatus.INCOMPLETE, MeasuredStatus.DEMOTED}
)


def dump_measured_routing(artifact: MeasuredRouting) -> str:
    """Serialize a :class:`MeasuredRouting` back to artifact TOML.

    The inverse of :func:`load_measured_routing`, so the tier and the
    **Calibration** that authors it share one shape rather than two that agree by
    convention. Emits only the keys the record's own state permits, so a writer
    cannot produce a file its reader rejects.
    """
    lines = [f"schema_version = {SCHEMA_VERSION}"]
    if artifact.provenance is not None:
        provenance = artifact.provenance
        lines += [
            "",
            "[provenance]",
            f"cli_version = {_toml_str(provenance.cli_version)}",
            f"calibrated_at = {_toml_str(provenance.calibrated_at)}",
            f"candidate_count = {provenance.candidate_count}",
            "gate_loops = ["
            + ", ".join(_toml_str(loop) for loop in provenance.gate_loops)
            + "]",
        ]
        # Both or neither, matching the reader: a writer that emitted
        # `classifier_model = ""` would invent a pin nobody classified under, and
        # the comparison would read it as a change.
        if provenance.classifier_model is not None:
            lines += [
                f"classifier_model = {_toml_str(provenance.classifier_model)}",
                f"classifier_effort = {_toml_str(provenance.classifier_effort or '')}",
            ]
    for key, entry in artifact.entries.items():
        table = f"routing.{_toml_key(key)}"
        lines += ["", f"[{table}]", f"status = {_toml_str(entry.status.value)}"]
        for name in sorted(_REQUIRED_KEYS[entry.status] - {"status"}):
            lines.append(f"{name} = {_toml_scalar(getattr(entry, name))}")
        for rung in entry.rungs:
            lines += [
                "",
                f"[[{table}.rung]]",
                f"model = {_toml_str(rung.model)}",
                f"effort = {_toml_str(rung.effort)}",
                f"passed = {rung.passed}",
                f"total = {rung.total}",
                f"credits = {_toml_scalar(rung.credits)}",
            ]
        for task in entry.proving_tasks:
            lines += [
                "",
                f"[[{table}.proving_task]]",
                f"issue = {task.issue}",
                f"base_commit = {_toml_str(task.base_commit)}",
                f"oracle_commit = {_toml_str(task.oracle_commit)}",
            ]
    return "\n".join(lines) + "\n"


def write_measured_routing(repo_root: Path, artifact: MeasuredRouting) -> None:
    """Write the artifact into ``repo_root``'s project scope dir, creating it."""
    path = measured_routing_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_measured_routing(artifact), encoding="utf-8")


#: The characters a TOML basic string must escape, beyond the two obvious ones.
_TOML_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _toml_str(value: str) -> str:
    """Encode a TOML basic string, escaping every character that needs it.

    A partial escape would let the reader accept a value the writer then emits
    as illegal TOML — the two halves of one seam disagreeing about the same
    string.
    """
    out = ['"']
    for char in value:
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _toml_key(key: str) -> str:
    """Encode one table-header key segment.

    A **Task type** key is one of the seven closed label suffixes, so it may hold a
    dot — and a bare ``api.backend`` would be written as two table levels and
    read back as something the artifact never said. Always quoting the segment
    keeps the key one key.
    """
    return _toml_str(key)


def _toml_scalar(value: object) -> str:
    if isinstance(value, Enum):
        # A closed vocabulary is written as its value, exactly as `status` is —
        # never as its Python repr, which no reader of this artifact accepts.
        return _toml_scalar(value.value)
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, float):
        return repr(value)
    return str(value)
