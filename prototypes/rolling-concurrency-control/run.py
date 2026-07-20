#!/usr/bin/env python3
"""PROTOTYPE — TUI/report shell for bounded rolling concurrency control."""

from __future__ import annotations

import sys

import model

B = "\x1b[1m"
D = "\x1b[2m"
R = "\x1b[0m"
CLR = "\x1b[2J\x1b[H"


def getch() -> str:
    if not sys.stdin.isatty():
        value = sys.stdin.readline()
        return value[:1] if value else "q"
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _bar(value: int, total: int, width: int = 10) -> str:
    filled = round(width * value / max(1, total))
    return "█" * filled + "░" * (width - filled)


def render(state: model.State, note: str = "") -> str:
    c = state.config
    cts = model.counts(state)
    out = [
        f"{B}ROLLING CONCURRENCY CONTROL — THROWAWAY PROTOTYPE{R}  t={state.clock}",
        (
            f"policy={B}{c.policy}{R}  configured Lane cap={B}{c.user_lane_cap}{R}  "
            f"effective={B}{state.effective_cap}{R}  Integration high-water="
            f"{B}{c.integration_high_water}{R}"
        ),
        (
            f"{D}configured cap is only an upper bound; Integration is event-driven "
            f"FIFO; full admission parks the Lane{R}"
        ),
    ]
    if state.degraded_signals:
        out.append(
            f"\x1b[33mSAFE FALLBACK: external signal missing; recovery ceiling "
            f"={min(c.user_lane_cap, c.safe_lane_cap)}\x1b[0m"
        )
    if note:
        out.append(f"{B}\x1b[33m>> {note}{R}")
    out.extend(
        [
            "",
            f"{B}PIPELINE{R}",
            (
                f"  active={cts['active']}  parked={cts['parked']}  "
                f"admitted={cts['admitted']}  integrating={cts['integrating']}  "
                f"auto-resolution={cts['autores']}  pool={len(state.pool)}"
            ),
        ]
    )
    for index, unit in enumerate(state.lanes):
        if unit is None:
            out.append(f"  L{index + 1} {D}idle{R}")
        elif unit.phase == model.SETUP:
            done = unit.spec.setup - unit.setup_left
            out.append(
                f"  L{index + 1} {unit.spec.key} SETUP "
                f"{_bar(done, unit.spec.setup, 5)} {done}/{unit.spec.setup}"
            )
        elif unit.phase == model.WORK:
            done = unit.spec.work - unit.work_left
            out.append(
                f"  L{index + 1} {unit.spec.key} WORK  "
                f"{_bar(done, unit.spec.work)} {done}/{unit.spec.work} "
                f"{D}cut@v{unit.base_at_cut}{R}"
            )
        else:
            out.append(
                f"  L{index + 1} {unit.spec.key} \x1b[31mPARKED\x1b[0m "
                f"{unit.parked_ticks}t"
            )
    out.append("")
    if state.integrating is None:
        out.append(f"{B}INTEGRATION{R} {D}idle{R}")
    else:
        out.append(
            f"{B}INTEGRATION{R} {state.integrating.spec.key} "
            f"{state.integration_mode} left={state.service_left}"
        )
    fifo = " ".join(unit.spec.key for unit in state.admitted) or "empty"
    out.append(
        f"  admitted FIFO: {fifo}  "
        f"({cts['integration_wip']}/{c.integration_high_water})"
    )
    out.extend(
        [
            "",
            f"{B}PRESSURE{R}",
            (
                f"  429 window={sum(state.recent_429)}  "
                f"credit window avg="
                f"{sum(state.recent_credit) / max(1, len(state.recent_credit)):.1f}/t  "
                f"host window avg="
                f"{sum(state.recent_host) / max(1, len(state.recent_host)):.1f}"
            ),
            (
                f"  credits={state.credit_total:.1f} "
                f"(wasted={state.credit_wasted:.1f})  "
                f"429={state.throttle_events}  conflicts={state.conflicts}  "
                f"auto attempts={state.autores_attempts}"
            ),
            "",
            f"{B}LAST REACTIONS{R}",
        ]
    )
    if not state.reactions:
        out.append(f"  {D}none{R}")
    for reaction in state.reactions[-4:]:
        out.append(
            f"  t{reaction.tick:03d} cap {reaction.before}->{reaction.after}: "
            f"{reaction.cause}"
        )
    out.extend(
        [
            "",
            f"{B}[space]{R} step  {B}[r]{R} run  {B}[p]{R} fixed/adaptive  "
            f"{B}[u]{R} signal availability",
            f"{B}[+/-]{R} configured cap  {B}[h/j]{R} high-water  "
            f"{B}[n]{R} new scenario  {B}[x]{R} report  {B}[q]{R} quit",
        ]
    )
    return "\n".join(out)


def _table(metrics: list[model.Metrics]) -> str:
    width = 35
    col = 26
    rows = [
        ("Makespan", [m.makespan for m in metrics], "{}"),
        ("Landed / fallbacks", [(m.landed, m.fallbacks) for m in metrics], "pair"),
        ("Active avg / peak", [(m.avg_active, m.peak_active) for m in metrics], "fpair"),
        ("Parked avg / peak", [(m.avg_parked, m.peak_parked) for m in metrics], "fpair"),
        ("Admitted FIFO peak", [m.peak_admitted for m in metrics], "{}"),
        ("Integration busy / full", [(m.integration_util, m.integration_full) for m in metrics], "ppair"),
        ("Auto-resolution ticks / attempts", [(m.autores_ticks, m.autores_attempts) for m in metrics], "pair"),
        ("429 throttles", [m.throttle_events for m in metrics], "{}"),
        ("AI credits / wasted", [(m.credits, m.wasted_credits) for m in metrics], "ffpair"),
        ("Integration wait p95 / max", [(m.p95_wait, m.max_wait) for m in metrics], "pair"),
        ("Max branch staleness / conflicts", [(m.max_staleness, m.conflicts) for m in metrics], "pair"),
        ("Host peak / overload ticks", [(m.host_peak, m.host_overload_ticks) for m in metrics], "ffpair"),
        ("Setup peak / total session peak", [(m.peak_setup, m.peak_total_sessions) for m in metrics], "pair"),
        ("Effective cap min..max / final", [(m.cap_min, m.cap_max, m.cap_final) for m in metrics], "triple"),
        ("Contractions / recoveries", [(m.contractions, m.recoveries) for m in metrics], "pair"),
        ("Serial drain wait / serial turns", [(m.serial_drain_wait, m.serial_turns) for m in metrics], "pair"),
        ("Post-serial full refill turns", [m.refill_turns for m in metrics], "{}"),
    ]
    lines = [" " * width + "".join(f"{m.label:>{col}}" for m in metrics)]
    lines.append("-" * (width + col * len(metrics)))
    for name, values, fmt in rows:
        cells = []
        for value in values:
            if fmt == "pair":
                text = f"{value[0]} / {value[1]}"
            elif fmt == "fpair":
                text = f"{value[0]:.1f} / {value[1]}"
            elif fmt == "ffpair":
                text = f"{value[0]:.1f} / {value[1]:.1f}"
            elif fmt == "ppair":
                text = f"{value[0]:.0%} / {value[1]:.0%}"
            elif fmt == "triple":
                text = f"{value[0]}..{value[1]} / {value[2]}"
            else:
                text = fmt.format(value)
            cells.append(f"{text:>{col}}")
        lines.append(f"{name:<{width}}" + "".join(cells))
    return "\n".join(lines)


def _reaction_table(state: model.State) -> str:
    lines = [
        "tick  cap     trigger                                      "
        "active parked admit integ auto 429win credit host",
        "-" * 112,
    ]
    for reaction in state.reactions:
        lines.append(
            f"{reaction.tick:>4}  {reaction.before}->{reaction.after:<3} "
            f"{reaction.cause:<44} "
            f"{reaction.active:>3} {reaction.parked:>6} {reaction.admitted:>5} "
            f"{reaction.integrating:>5} {reaction.autores:>4} "
            f"{reaction.recent_429:>6} {reaction.recent_credit:>6.1f} "
            f"{reaction.recent_host:>4.1f}"
        )
    return "\n".join(lines)


def _policy_reactions() -> str:
    return """signal / state                         fixed cap                 bounded adaptive
------------------------------------------------------------------------------------------------
Integration WIP reaches H=2              stop refill; finisher parks stop refill; finisher parks
H full + parked Lane for 4/6 ticks       no cap change              cap -1 after cooldown
>=3 observed 429s in 6 ticks             no cap change              cap -2 after cooldown
AI-credit burn >110% target for 6 ticks  no cap change              cap -1 after cooldown
host/setup load >102% for 6 ticks        no cap change              cap -1 after cooldown
auto-resolution active                   outside Lane cap           outside cap; counts as pressure
parked completed work                    consumes its Lane slot      consumes its Lane slot
10 healthy ticks after cooldown          no cap change              cap +1, never above user cap
any external signal unavailable          unchanged user cap         freeze at min(cap,3), hard H only
all external signals unavailable         unchanged user cap         same static-safe behavior"""


def _trigger_probes(scenario: model.Scenario, cap: int) -> str:
    probes = [
        (
            "429",
            model.Config.adaptive(
                cap,
                4,
                safe_lane_cap=cap,
                host_capacity=99,
                credit_target_per_tick=99,
            ),
            "429 pressure",
        ),
        (
            "AI-credit burn",
            model.Config.adaptive(
                cap,
                4,
                safe_lane_cap=cap,
                rate_cost_work=0,
                rate_cost_auto=0,
                host_capacity=99,
                credit_target_per_tick=2.0,
            ),
            "AI-credit",
        ),
        (
            "host/setup",
            model.Config.adaptive(
                cap,
                4,
                safe_lane_cap=cap,
                rate_cost_work=0,
                rate_cost_auto=0,
                host_capacity=3.0,
                credit_target_per_tick=99,
            ),
            "host/setup",
        ),
        (
            "Integration saturation",
            model.Config.adaptive(
                cap,
                2,
                safe_lane_cap=cap,
                rate_cost_work=0,
                rate_cost_auto=0,
                host_capacity=99,
                credit_target_per_tick=99,
            ),
            "Integration",
        ),
    ]
    lines = [
        "probe                    first matching reaction                 "
        "makespan 429 credits p95wait parked-peak",
        "-" * 104,
    ]
    for name, config, match in probes:
        state = model.run_to_completion(scenario, config)
        metric = model.metrics(state)
        reaction = next((r for r in state.reactions if match in r.cause), None)
        reaction_text = (
            f"t{reaction.tick} cap {reaction.before}->{reaction.after}"
            if reaction
            else "not triggered"
        )
        lines.append(
            f"{name:<24}{reaction_text:<37}"
            f"{metric.makespan:>8} {metric.throttle_events:>3} "
            f"{metric.credits:>7.1f} {metric.p95_wait:>7} "
            f"{metric.peak_parked:>11}"
        )
    return "\n".join(lines)


def _autores_budget_report() -> str:
    scenario = model.Scenario(
        seed=2,
        issues=(
            model.IssueSpec("A", 1, 1, 18, 0.0, 2),
            model.IssueSpec("B", 1, 1, 3, 1.0, 1),
            model.IssueSpec("C", 1, 1, 3, 1.0, 1),
            model.IssueSpec("D", 1, 1, 3, 1.0, 1),
            model.IssueSpec("E", 1, 1, 3, 1.0, 1),
            model.IssueSpec("F", 1, 1, 3, 1.0, 1),
        ),
    )
    common = dict(
        safe_lane_cap=6,
        rate_cost_work=0,
        rate_cost_auto=0,
        host_capacity=99,
        credit_target_per_tick=99,
        serial_request_at=-1,
    )
    outside = model.run_to_completion(
        scenario,
        model.Config.adaptive(
            6, 2, autores_counts_against_lane_cap=False, **common
        ),
        max_ticks=120,
    )
    shared = model.run_to_completion(
        scenario,
        model.Config.adaptive(
            6, 2, autores_counts_against_lane_cap=True, **common
        ),
        max_ticks=120,
    )

    def row(name: str, state: model.State) -> str:
        metric = model.metrics(state)
        cts = model.counts(state)
        return (
            f"{name:<33}{str(model.is_done(state)):<10}"
            f"{metric.makespan:>5} {metric.landed:>6} {cts['parked']:>6} "
            f"{cts['admitted']:>8} {cts['integrating']:>11} "
            f"{metric.autores_wait_ticks:>9}"
        )

    return "\n".join(
        [
            "Six-item edge case: H=2, cap contracts while four finishers park.",
            "policy                         complete  tick landed parked "
            "admitted integrating auto-wait",
            "-" * 91,
            row("auto-resolution outside Lane cap", outside),
            row("hard shared Lane/auto budget", shared),
            (
                "Hard sharing deadlocks here: private Integration waits for a "
                "budget slot while four parked finishers retain Lane occupancy."
            ),
        ]
    )


def report(scenario: model.Scenario, cap: int = 6) -> str:
    fixed = model.run_to_completion(scenario, model.Config.fixed(cap, 2))
    adaptive = model.run_to_completion(scenario, model.Config.adaptive(cap, 2))
    unavailable = model.run_to_completion(
        scenario,
        model.Config.adaptive(
            cap,
            2,
            signals=model.SignalAvailability(False, False, False),
        ),
    )
    comparison = _table(
        [
            model.metrics(fixed),
            model.metrics(adaptive),
            model.metrics(unavailable),
        ]
    )

    high_water_metrics = []
    for high_water in range(1, 5):
        state = model.run_to_completion(
            scenario, model.Config.adaptive(cap, high_water)
        )
        high_water_metrics.append(
            model.metrics(state, f"Adaptive H={high_water}")
        )

    return "\n\n".join(
        [
            (
                f"PROTOTYPE REPORT — {scenario.n_issues} Parallel-safe work items, "
                f"configured Lane cap={cap}, seed={scenario.seed}\n"
                "H counts the integrating candidate plus FIFO-admitted candidates; "
                "parked finishers remain in Lane slots."
            ),
            "POLICY COMPARISON\n" + comparison,
            "INTEGRATION HIGH-WATER SWEEP\n" + _table(high_water_metrics),
            "AUTO-RESOLUTION BUDGET EDGE CASE\n" + _autores_budget_report(),
            "ISOLATED SIGNAL PROBES\n" + _trigger_probes(scenario, cap),
            "ADAPTIVE REACTION LOG\n" + _reaction_table(adaptive),
            "PROPOSED REACTION TABLE\n" + _policy_reactions(),
        ]
    )


def main() -> int:
    scenario = model.make_scenario()
    if "--report" in sys.argv or "--compare" in sys.argv:
        print(report(scenario))
        return 0

    cap = 6
    high_water = 2
    seed = scenario.seed
    signals = model.SignalAvailability()
    policy = "adaptive"

    def rebuild() -> model.State:
        config = (
            model.Config.fixed(cap, high_water)
            if policy == "fixed"
            else model.Config.adaptive(
                cap, high_water, signals=signals
            )
        )
        return model.initial_state(scenario, config)

    state = rebuild()
    note = "press [space] to step or [x] for the complete report"
    while True:
        sys.stdout.write(CLR + render(state, note) + "\n")
        sys.stdout.flush()
        note = ""
        key = getch()
        if key in ("q", "\x03"):
            break
        if key in (" ", "\r", "\n"):
            model.step(state)
        elif key == "r":
            while not model.is_done(state):
                model.step(state)
            note = "run complete"
        elif key == "p":
            policy = "fixed" if policy == "adaptive" else "adaptive"
            state = rebuild()
            note = f"policy -> {policy}; restarted"
        elif key == "u":
            signals = (
                model.SignalAvailability(False, False, False)
                if signals.complete
                else model.SignalAvailability()
            )
            state = rebuild()
            note = f"external signals complete={signals.complete}; restarted"
        elif key in ("+", "="):
            cap = min(12, cap + 1)
            state = rebuild()
            note = f"configured Lane cap -> {cap}; restarted"
        elif key == "-":
            cap = max(1, cap - 1)
            state = rebuild()
            note = f"configured Lane cap -> {cap}; restarted"
        elif key == "h":
            high_water = min(6, high_water + 1)
            state = rebuild()
            note = f"Integration high-water -> {high_water}; restarted"
        elif key == "j":
            high_water = max(1, high_water - 1)
            state = rebuild()
            note = f"Integration high-water -> {high_water}; restarted"
        elif key == "n":
            seed += 1
            scenario = model.make_scenario(seed=seed)
            state = rebuild()
            note = f"scenario seed -> {seed}; restarted"
        elif key == "x":
            sys.stdout.write(CLR + report(scenario, cap) + "\n\n")
            sys.stdout.flush()
            getch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
