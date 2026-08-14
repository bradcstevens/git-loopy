"""``git_loopy.routing_scope`` — Routing is a Parallel-mode feature (#379, ADR-0027).

One rule lives here, and everything built on **Routing** reads it from this one
place: a **Routed pair** is resolved *per issue*, and only a **Lane** works one
issue per session — so at ``parallel == 1`` the whole precedence chain, the
**Measured routing** tier included, is *inert*.

It is not merely unimplemented in serial but incoherent there: the serial loop
folds the whole **Pool** into a single prompt on the run-wide pair, so there is
no per-issue pair for any tier to supply. Making it route would abandon
ADR-0008's promise that serial runs byte-for-byte unchanged, which is not on the
table.

Design notes:

* **Declared, never discovered** (ADR-0027). Silence is the worst option
  available: it yields a feature that appears to work, commits evidence, and
  changes nothing. An operator can otherwise spend hours on a **Calibration**
  and observe nothing at all.
* **One rule, one module.** The scope question has several askers — the config
  reporting surfaces today, ``git-loopy calibrate`` (#372) and **Demotion**
  (#366) next — and a rule each asker restates is a rule they can disagree
  about. :func:`routing_in_force` is the only place ``parallel`` is compared.
* **The reason is not "serial has no Pickup".** That was true when #364 wrote
  it and is not any more: ADR-0032 gave a serial **Iteration** a pickup of its
  own, and ``CONTEXT.md`` now says every unit of work has one. The narrower
  fact — one session, many issues, one pair — is what actually scopes routing,
  and it survives ADR-0032 landing.
* **Pure over an ``int``.** Nothing here reads Config, the artifact or the
  environment, so a caller resolves its own ``parallel`` and this stays pinnable
  without a repository. It spends no **AI Credit** and starts no Calibration.
* **Says how to fix it.** A refusal that names the requirement without naming
  the flag leaves the operator exactly as stuck as silence would have.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "SERIAL_PARALLELISM",
    "PARALLEL_MODE_HINT",
    "ROUTING_SCOPE_REASON",
    "SERIAL_INERT_NOTE",
    "routing_in_force",
    "calibration_refusal",
]

#: The ``parallel`` value that *is* serial. ``1`` is the default
#: (:class:`~git_loopy.config.RunConfig`), so everything below describes the
#: out-of-the-box run rather than an unusual one.
SERIAL_PARALLELISM: Final[int] = 1

#: How an operator turns the inert chain on. Parallel mode is opt-in and has no
#: Config key — it is resolved from the flag or the env var only
#: (:func:`git_loopy.cli.resolve_parallel`), which is why neither
#: ``config set`` nor the artifact appears here.
PARALLEL_MODE_HINT: Final[str] = (
    "Enable Parallel mode with `--parallel N` (or GIT_LOOPY_MAX_PARALLEL=N)."
)

#: The shared clause. The note and the refusal below say the same thing for the
#: same reason, so they say it in the same words — and a third asker (**Demotion**,
#: #366) says it in those words too.
#:
#: It is deliberately *not* phrased as "serial has no **Pickup**". ADR-0032 gave
#: a serial **Iteration** a pickup of its own, so that reading — true when #364
#: wrote it — is now false in the glossary's own vocabulary. What actually scopes
#: routing is narrower and survives ADR-0032: a **Routed pair** is per issue, and
#: only a **Lane** runs one issue per session.
ROUTING_SCOPE_REASON: Final[str] = (
    "a Routed pair is resolved per issue, and only a Parallel-mode Lane works "
    "one issue per session"
)

#: What a reporting surface says beside a **Routed pair** it has just printed.
#: The value is still reported — suppressing it would answer "why is this model
#: set?" with an absence — but it is reported as having no effect.
SERIAL_INERT_NOTE: Final[str] = (
    f"{ROUTING_SCOPE_REASON}. In serial mode "
    f"(parallel = {SERIAL_PARALLELISM}) one Iteration is handed the whole Pool "
    f"and runs on the run-wide pair, so every tier is inert. "
    f"{PARALLEL_MODE_HINT}"
)


def routing_in_force(parallel: int) -> bool:
    """Whether a **Routed pair** resolved at this parallelism takes effect.

    ``False`` in serial, where one **Iteration** is handed the whole **Pool** and
    runs on the run-wide pair, so there is no per-issue pair for the chain to
    supply. This is the predicate a feature built on Routing asks before doing
    anything an inert chain would waste — spending on a **Calibration** (#372),
    or writing a **Demotion** into the committed artifact (#366).
    """
    return parallel > SERIAL_PARALLELISM


def calibration_refusal(parallel: int) -> str | None:
    """The refusal a **Calibration** must print at this parallelism, or ``None``.

    ``None`` is the permission to proceed, so the decision is made here rather
    than by a caller remembering to compare ``parallel`` itself. The message
    names Parallel mode as the requirement *and* how to enable it, because a
    Calibration refused without a way forward is only a more legible silence.
    """
    if routing_in_force(parallel):
        return None
    return (
        f"a Calibration measures the Routed pair a Task type is worked on, and "
        f"{ROUTING_SCOPE_REASON}. This run is serial (parallel = {parallel}), "
        f"so nothing a Calibration measured would take effect and it would "
        f"spend AI Credits for no change. {PARALLEL_MODE_HINT}"
    )
