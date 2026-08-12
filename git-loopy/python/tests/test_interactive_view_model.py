"""Import-discipline coverage for the toolkit-neutral Dashboard projection."""

from __future__ import annotations

import ast
from pathlib import Path

from git_loopy.interactive import view_model


def test_view_model_module_has_no_renderer_dependency() -> None:
    source = Path(view_model.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {
        "__future__",
        "decimal",
        "typing",
        "git_loopy.denomination",
        "git_loopy.interactive.state",
        "git_loopy.ui.summary",
    }
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            seen.add(node.module)

    assert not seen - allowed
    assert "textual" not in seen
    assert "rich" not in seen

def test_the_two_cost_unavailability_reasons_survive_the_projection() -> None:
    """*No billing telemetry* and *cannot report Cost* stay separable (ADR-0026).

    Both arrive at a renderer as the same unknown figure, and only one of them
    is worth waiting out. The nulled figure cannot carry the difference — the
    **Wrapper contract** lets a producer signal an unobservable measurement by
    omitting a key *or* by nulling it — so the Run-start declaration is the only
    honest source, and the projection states it once per **Run**.
    """
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    unable = LiveRunState()
    unable.render(
        {"type": "wrapper.run.start", "insight_capabilities": {"cost": False}}
    )
    projected = project_run_view(unable, None, issue=1)
    assert projected["dashboard"]["header"]["cost"] == {"availability": "unavailable"}

    unbilled = LiveRunState()
    unbilled.render(
        {"type": "wrapper.run.start", "insight_capabilities": {"cost": True}}
    )
    projected = project_run_view(unbilled, None, issue=1)
    assert projected["dashboard"]["header"]["cost"] == {"availability": "available"}

    # And a Run that has seen no manifest has been told nothing about Cost,
    # which is neither of the two.
    projected = project_run_view(LiveRunState(), None, issue=1)
    assert projected["dashboard"]["header"]["cost"] == {"availability": "not_declared"}


def test_the_rate_card_is_declared_beside_cost_and_never_costs_a_figure() -> None:
    """The card is provenance, not arithmetic (ADR-0026).

    Its prices are denominated in the same **AI Credits** the harness already
    billed, so nothing derives from it: a **Run** that resolved no card reports
    Cost in full, and *no rate card* is a statement about the Run's own prices
    rather than a third kind of unknown Cost.
    """
    from git_loopy.interactive.state import LiveRunState
    from git_loopy.interactive.view_model import project_run_view

    state = LiveRunState()
    state.render(
        {
            "type": "wrapper.run.start",
            "insight_capabilities": {"cost": True, "rate_card": False},
            "rate_card": None,
        }
    )
    state.render({"type": "wrapper.iteration.start", "iter": 1})
    state.render({"type": "wrapper.issue.activated", "iter": 1, "issue": 42})
    state.render(
        {
            "type": "usage.tokens",
            "iter": 1,
            "input": 100,
            "output": 50,
            "credits": 1.5,
            "premium_requests": 2.0,
        }
    )

    projected = project_run_view(state, None, issue=42)
    header = projected["dashboard"]["header"]
    assert header["rate_card"] == {"availability": "unavailable"}
    assert header["cost"] == {"availability": "available"}
    row = next(
        row for row in projected["dashboard"]["queue"]["rows"] if row["issue"] == 42
    )
    assert row["credits"] == 1.5
    assert row["premium_requests"] == 2.0
