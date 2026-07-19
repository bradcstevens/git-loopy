#!/usr/bin/env python3
"""PROTOTYPE — throwaway TUI shell over sim.py. Do NOT ship into production.

Run:  python3 run.py            # interactive stepper
      python3 run.py --compare  # headless side-by-side table, then exit
      python3 run.py --selftest # invariant checks, then exit

Drive the stepper by hand: press [space] to advance one tick and watch the
Integration queue build. Toggle Wave<->Rolling and backpressure to feel the
difference. [c] prints the side-by-side numbers on the current scenario.
"""

from __future__ import annotations

import sys

import sim

B = "\x1b[1m"   # bold
D = "\x1b[2m"   # dim
R = "\x1b[0m"   # reset
CLR = "\x1b[2J\x1b[H"


def bar(done: int, total: int, width: int = 10) -> str:
    total = max(1, total)
    done = max(0, min(done, total))
    f = int(round(width * done / total))
    return "█" * f + "░" * (width - f)


def getch() -> str:
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return line[:1] if line else "q"
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def render(s: sim.State, note: str = "") -> str:
    c = s.config
    m = sim.metrics(s)
    out = []
    out.append(f"{B}ROLLING-DISPATCH SIM{R}  {D}git-loopy Parallel mode "
               f"(ticket #133 / map #131){R}   t={B}{s.clock}{R}")
    bp = (f"ON(cap {c.wip_cap})" if c.backpressure else "off")
    out.append(f"policy={B}{c.policy.upper()}{R}  N={B}{c.n_lanes}{R}  "
               f"backpressure={bp}  serial_setup={'on' if c.serial_setup else 'off'}  "
               f"ratelimit={'on' if c.rate_limit_on else 'off'}")
    out.append(f"{D}scenario: {s.scenario.n_issues} issues seed={s.scenario.seed}"
               f"   pool_remaining={len(s.pool)}   "
               f"{'DONE' if sim.is_done(s) else 'running'}{R}")
    if note:
        out.append(f"{B}\x1b[33m>> {note}{R}")
    out.append("")

    out.append(f"{B}SLOTS{R} {D}(agent Lanes — WORK draws the API rate bucket){R}")
    for i, u in enumerate(s.slots):
        if u is None:
            reason = ""
            if not s.pool:
                reason = "pool empty"
            elif c.policy == "wave":
                reason = "waiting for barrier / integration"
            elif c.backpressure and sim._wip(s) >= c.wip_cap:
                reason = "backpressure: WIP full"
            out.append(f"  L{i}  {D}--idle--{R}  {D}{reason}{R}")
        elif u.phase == sim.SETUP:
            out.append(f"  L{i}  #{u.num}  {B}SETUP{R} {bar(u.setup_total-u.setup_left, u.setup_total,4)}"
                       f" {u.setup_total-u.setup_left}/{u.setup_total}")
        elif u.phase == sim.READY:
            out.append(f"  L{i}  #{u.num}  {D}READY (holding for Wave barrier){R}")
        elif u.phase == sim.WORK:
            out.append(f"  L{i}  #{u.num}  {B}WORK{R}  {bar(u.work_total-u.work_left, u.work_total)}"
                       f" {u.work_total-u.work_left}/{u.work_total}   {D}[cut@base v{u.base_at_cut}]{R}")
    out.append("")

    out.append(f"{B}INTEGRATION{R} {D}(serialized — one Lane at a time, ADR-0009){R}")
    if s.integ is not None:
        u = s.integ
        served = (u.integ_base + u.attempts * c.resolve_cost) - u.integ_left
        tag = (f"\x1b[31mCONFLICT x{u.attempts} -> {u.destiny}\x1b[0m"
               if u.conflicted else f"{D}clean{R}")
        out.append(f"  serving #{u.num}  {bar(served, u.integ_base + u.attempts*c.resolve_cost)}"
                   f" left {u.integ_left}   {tag}")
    else:
        out.append(f"  {D}(idle){R}")
    qs = " ".join(f"#{u.num}(w{u.wait_ticks})" for u in s.queue) or f"{D}empty{R}"
    depth_c = "\x1b[31m" if len(s.queue) >= c.n_lanes else ""
    out.append(f"  queue {depth_c}depth {len(s.queue)}{R}: {qs}"
               f"   {D}max-depth-so-far {m.max_qdepth}, worst-wait {m.max_wait}{R}")
    out.append("")

    budget = (f"{s.bucket}/{c.rate_capacity} {D}(+{c.rate_refill}/t, "
              f"{c.rate_cost}/lane){R}") if c.rate_limit_on else f"{D}unlimited{R}"
    out.append(f"{B}RATE{R} bucket {budget}   {D}429-throttles: {m.throttle_ticks}{R}")
    landed = " ".join(f"#{n}" for n in s.landed) or "—"
    fb = (f"   \x1b[31mfallbacks: {' '.join('#'+str(n) for n in s.fallbacks)}\x1b[0m"
          if s.fallbacks else "")
    out.append(f"{B}BASE{R} landed({len(s.landed)}/{s.scenario.n_issues}): {landed}"
               f"   {D}version={s.base_version}{R}{fb}")
    out.append("")

    out.append(f"{B}METRICS{R}  work-util {B}{m.work_util:4.0%}{R}  idle {m.idle_ticks}"
               f"  integ-busy {B}{m.integ_util:4.0%}{R}  "
               f"conflicts {m.conflicts}  k-resolves {m.k_resolves}  "
               f"fallbacks {m.fallbacks}")
    out.append(f"{D}{'-'*72}{R}")
    for e in s.events[-6:]:
        out.append(f"{D}  {e}{R}")
    out.append("")
    out.append(f"{B}[space]{R}step {B}[r]{R}run {B}[m]{R}policy {B}[b]{R}backpressure "
               f"{B}[e]{R}serial-setup {B}[l]{R}ratelimit")
    out.append(f"{B}[+/-]{R}N  {B}[n]{R}new-scenario  {B}[c]{R}compare-both  {B}[q]{R}quit")
    return "\n".join(out)


def compare_table(scenario: sim.Scenario, n_lanes: int) -> str:
    runs = [
        sim.run_to_completion(scenario, sim.Config.for_policy("wave", n_lanes)),
        sim.run_to_completion(scenario, sim.Config.for_policy("rolling", n_lanes)),
        sim.run_to_completion(scenario, sim.Config.for_policy("rolling", n_lanes, backpressure=True)),
    ]
    ms = [sim.metrics(s) for s in runs]
    rows = [
        ("Makespan (ticks, lower=faster)", [m.makespan for m in ms], "{:d}"),
        ("Slot work-utilization",          [m.work_util for m in ms], "{:.0%}"),
        ("Lane idle ticks",                [m.idle_ticks for m in ms], "{:d}"),
        ("Integration-server busy",        [m.integ_util for m in ms], "{:.0%}"),
        ("Max integ queue depth",          [m.max_qdepth for m in ms], "{:d}"),
        ("Avg integ queue depth",          [m.avg_qdepth for m in ms], "{:.1f}"),
        ("Worst queue wait (ticks)",       [m.max_wait for m in ms], "{:d}"),
        ("Rate-limit throttles (429)",     [m.throttle_ticks for m in ms], "{:d}"),
        ("Stale conflicts",                [m.conflicts for m in ms], "{:d}"),
        ("Auto-resolution attempts",       [m.k_resolves for m in ms], "{:d}"),
        ("Fallbacks (work LOST)",          [m.fallbacks for m in ms], "{:d}"),
        ("Landed green / total",           [(m.landed, m.total) for m in ms], "pair"),
    ]
    labels = [m.policy_label for m in ms]
    w = 30
    lines = [f"{B}Scenario: {scenario.n_issues} issues, N={n_lanes}, "
             f"seed={scenario.seed}{R}"]
    header = " " * w + "".join(f"{B}{lab:>13}{R}" for lab in labels)
    lines.append(header)
    lines.append(D + "-" * (w + 13 * len(labels)) + R)
    for name, vals, fmt in rows:
        cells = []
        for v in vals:
            if fmt == "pair":
                raw = f"{v[0]}/{v[1]}"
            else:
                raw = fmt.format(v)
            cells.append(f"{raw:>13}")
        lines.append(f"{name:<{w}}" + "".join(cells))
    return "\n".join(lines)


def selftest() -> int:
    sc = sim.make_scenario()
    results = {}
    for cfg in (sim.Config.for_policy("wave", 4),
                sim.Config.for_policy("rolling", 4),
                sim.Config.for_policy("rolling", 4, backpressure=True)):
        s = sim.run_to_completion(sc, cfg)
        accounted = len(s.landed) + len(s.fallbacks)
        assert accounted == sc.n_issues, (cfg.policy, accounted, sc.n_issues)
        assert sim.is_done(s), "did not terminate"
        m = sim.metrics(s)
        results[m.policy_label] = m
        print(f"OK  {m.policy_label:<14} makespan={m.makespan:>3} "
              f"landed={m.landed} fallback={m.fallbacks} "
              f"maxq={m.max_qdepth} worst-wait={m.max_wait} "
              f"throttle={m.throttle_ticks}")
    # Backpressure must never deepen the Integration queue vs plain rolling.
    assert results["Rolling+BP"].max_qdepth <= results["Rolling"].max_qdepth, \
        "backpressure failed to bound the queue"
    assert results["Rolling+BP"].avg_qdepth <= results["Rolling"].avg_qdepth + 1e-9
    # Rolling should beat the barrier Wave on makespan (keeps Lanes busy).
    assert results["Rolling"].makespan < results["Wave (today)"].makespan, \
        "rolling did not beat the barrier wave"
    print("selftest passed")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if "--compare" in sys.argv:
        sc = sim.make_scenario()
        print(compare_table(sc, 3))
        return 0

    scenario = sim.make_scenario()
    seed = scenario.seed
    n = 3
    cfg = sim.Config.for_policy("rolling", n)
    s = sim.initial_state(scenario, cfg)
    note = "welcome — press [space] to step, [c] to compare, [q] to quit"

    def rebuild(newcfg: sim.Config) -> sim.State:
        return sim.initial_state(scenario, newcfg)

    while True:
        sys.stdout.write(CLR + render(s, note) + "\n")
        sys.stdout.flush()
        note = ""
        ch = getch()
        if ch in ("q", "\x03"):
            break
        elif ch in (" ", "s", "\r", "\n"):
            sim.step(s)
        elif ch == "r":
            guard = 0
            while not sim.is_done(s) and guard < 100000:
                sim.step(s)
                guard += 1
            note = "ran to completion"
        elif ch == "m":
            cfg = sim.Config.for_policy(
                "wave" if cfg.policy == "rolling" else "rolling", n,
                backpressure=cfg.backpressure)
            s = rebuild(cfg)
            note = f"policy -> {cfg.policy} (restarted)"
        elif ch == "b":
            cfg = sim.Config.for_policy(cfg.policy, n, backpressure=not cfg.backpressure)
            s = rebuild(cfg)
            note = f"backpressure -> {cfg.backpressure} (restarted)"
        elif ch == "e":
            cfg.serial_setup = not cfg.serial_setup
            s = rebuild(cfg)
            note = f"serial_setup -> {cfg.serial_setup} (restarted)"
        elif ch == "l":
            cfg.rate_limit_on = not cfg.rate_limit_on
            s = rebuild(cfg)
            note = f"rate_limit -> {cfg.rate_limit_on} (restarted)"
        elif ch in ("+", "="):
            n = min(12, n + 1)
            cfg = sim.Config.for_policy(cfg.policy, n, backpressure=cfg.backpressure)
            s = rebuild(cfg)
            note = f"N -> {n} (restarted)"
        elif ch == "-":
            n = max(1, n - 1)
            cfg = sim.Config.for_policy(cfg.policy, n, backpressure=cfg.backpressure)
            s = rebuild(cfg)
            note = f"N -> {n} (restarted)"
        elif ch == "n":
            seed += 1
            scenario = sim.make_scenario(seed=seed)
            s = rebuild(cfg)
            note = f"new scenario seed={seed} (restarted)"
        elif ch == "c":
            sys.stdout.write(CLR + compare_table(scenario, n)
                             + f"\n\n{D}(press any key to return){R}\n")
            sys.stdout.flush()
            getch()
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
