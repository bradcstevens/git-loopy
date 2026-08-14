"""Tests for :mod:`git_loopy.sources` — IssueSource Protocol + impls."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from git_loopy import gh as gh_module
from git_loopy import sources as sources_module
from git_loopy.sources import (
    AfkReadyItem,
    Completion,
    GitHubIssueSource,
    IssueSource,
    PrdsIssueSource,
    is_afk_ready,
    is_pr_afk_ready,
)
from tests.fakes import FakeGitHubClient


# --------------------------------------------------------------------------- #
# Shared helpers                                                              #
# --------------------------------------------------------------------------- #


def _silent_logger() -> logging.Logger:
    """A test logger with a NullHandler — silent but still log-API-shaped."""
    logger = logging.getLogger(f"git_loopy.tests.{id(object())}")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


@contextlib.contextmanager
def _capture(logger: logging.Logger) -> Iterator[list[logging.LogRecord]]:
    """Collect what ``logger`` emitted inside the block.

    ``caplog`` cannot see these: :func:`_silent_logger` sets
    ``propagate = False``, which is what keeps a source's diagnostics out of
    the test run's own output.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collector()
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


@dataclass(frozen=True)
class _FakeCommit:
    """Minimal stand-in for :class:`git_loopy.git.Commit`."""

    sha: str
    subject: str
    body: str = ""
    date: str = "2026-05-16"

    @property
    def message(self) -> str:
        return f"{self.subject}\n{self.body}" if self.body else self.subject


def _make_issue(
    number: int,
    *,
    body: str = "## Parent\n#1\n\n## What to build\nthing\n\n## Acceptance criteria\n- done",
    state: str = "OPEN",
    labels: list[str] | None = None,
    title: str | None = None,
    created_at: str = "",
    comments: tuple[gh_module.Comment, ...] = (),
) -> gh_module.Issue:
    return gh_module.Issue(
        number=number,
        title=title or f"Test issue {number}",
        body=body,
        labels=labels if labels is not None else ["ready-for-agent"],
        state=state,
        url=f"https://github.com/x/y/issues/{number}",
        created_at=created_at,
        comments=comments,
    )


# --------------------------------------------------------------------------- #
# is_afk_ready                                                                #
# --------------------------------------------------------------------------- #


class TestIsAfkReady:
    def test_returns_true_for_body_with_both_sections(self) -> None:
        body = "Intro\n\n## What to build\nthing\n\n## Acceptance criteria\n- foo"
        assert is_afk_ready(body) is True

    def test_returns_true_when_parent_omitted(self) -> None:
        # ``## Parent`` is OPTIONAL per the to-issues template; a slice that
        # omits it is still AFK-ready as long as it carries the two required
        # sections. Regression guard for parent-less slices being dropped.
        body = "## What to build\nthing\n\n## Acceptance criteria\n- foo"
        assert is_afk_ready(body) is True

    def test_returns_true_when_parent_present(self) -> None:
        # ``## Parent`` is allowed (and usual) — it just isn't required.
        body = "## Parent\n#1\n\n## What to build\nx\n\n## Acceptance criteria\n- foo"
        assert is_afk_ready(body) is True

    def test_returns_false_when_missing_what_to_build(self) -> None:
        body = "## Parent\n#1\n\n## Acceptance criteria\n- foo"
        assert is_afk_ready(body) is False

    def test_returns_false_when_missing_acceptance_criteria(self) -> None:
        body = "## What to build\nthing"
        assert is_afk_ready(body) is False

    def test_returns_false_for_empty_body(self) -> None:
        assert is_afk_ready("") is False

    def test_returns_false_when_sections_are_not_line_anchored(self) -> None:
        body = "blah ## What to build x ## Acceptance criteria done"
        assert is_afk_ready(body) is False

    def test_returns_true_when_sections_anchored_at_start_of_string(self) -> None:
        body = "## What to build\nthing\n## Acceptance criteria\n- bar"
        assert is_afk_ready(body) is True

    def test_returns_true_when_extra_text_follows_section_heading(self) -> None:
        # "## What to build" must be at the start of a line; extra text on
        # the same line after the heading is allowed because the regex isn't
        # end-anchored.
        body = "## What to build  (slice)\n\n## Acceptance criteria\n- bar"
        assert is_afk_ready(body) is True


# --------------------------------------------------------------------------- #
# afk_ready_exclusion — the discriminator's reason (issue #303)               #
# --------------------------------------------------------------------------- #


class TestAfkReadyExclusion:
    """``afk_ready_exclusion`` names *why* a candidate leaves the Pool.

    ``is_afk_ready`` stays the boolean projection of this function, so a Pool
    exclusion can never disagree with Pool membership — the drift that made
    the silent drop invisible in the first place.
    """

    def test_returns_none_when_afk_ready(self) -> None:
        body = "## What to build\nthing\n\n## Acceptance criteria\n- foo"
        assert sources_module.afk_ready_exclusion(body) is None

    def test_names_the_missing_what_to_build_section(self) -> None:
        body = "## Parent\n#1\n\n## Acceptance criteria\n- foo"
        assert (
            sources_module.afk_ready_exclusion(body)
            == sources_module.EXCLUSION_MISSING_WHAT_TO_BUILD
        )

    def test_names_the_missing_acceptance_criteria_section(self) -> None:
        body = "## What to build\nthing"
        assert (
            sources_module.afk_ready_exclusion(body)
            == sources_module.EXCLUSION_MISSING_ACCEPTANCE_CRITERIA
        )

    def test_names_both_sections_when_neither_is_present(self) -> None:
        body = "A bare planning document with no task sections."
        assert (
            sources_module.afk_ready_exclusion(body)
            == sources_module.EXCLUSION_MISSING_BOTH_SECTIONS
        )

    def test_empty_body_is_missing_both_sections(self) -> None:
        assert (
            sources_module.afk_ready_exclusion("")
            == sources_module.EXCLUSION_MISSING_BOTH_SECTIONS
        )

    @pytest.mark.parametrize(
        "body",
        [
            "## What to build\nthing\n\n## Acceptance criteria\n- foo",
            "## Parent\n#1\n\n## Acceptance criteria\n- foo",
            "## What to build\nthing",
            "",
            "blah ## What to build x ## Acceptance criteria done",
        ],
    )
    def test_is_afk_ready_is_the_boolean_projection(self, body: str) -> None:
        assert is_afk_ready(body) is (sources_module.afk_ready_exclusion(body) is None)


# --------------------------------------------------------------------------- #
# AfkReadyItem + Completion dataclass shape                                   #
# --------------------------------------------------------------------------- #


class TestDataclassShapes:
    def test_afk_ready_item_carries_int_ref_for_github(self) -> None:
        item = AfkReadyItem(ref=42, title="t", rendered_block="x")
        assert item.ref == 42
        assert isinstance(item.ref, int)

    def test_afk_ready_item_carries_str_ref_for_prds(self) -> None:
        item = AfkReadyItem(
            ref="prds/feat/001-x.md", title="t", rendered_block="x"
        )
        assert item.ref == "prds/feat/001-x.md"
        assert isinstance(item.ref, str)

    def test_afk_ready_item_is_frozen(self) -> None:
        item = AfkReadyItem(ref=1, title="t", rendered_block="x")
        with pytest.raises(Exception):
            item.ref = 99  # type: ignore[misc]

    def test_afk_ready_item_defaults_labels_to_empty_tuple(self) -> None:
        item = AfkReadyItem(ref=1, title="t", rendered_block="x")
        assert item.labels == ()

    def test_completion_defaults_shas_to_empty_tuple(self) -> None:
        c = Completion(ref=1, sha="deadbeef")
        assert c.shas == ()

    def test_completion_is_frozen(self) -> None:
        c = Completion(ref=1, sha="deadbeef")
        with pytest.raises(Exception):
            c.sha = "nope"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# IssueSource Protocol structural conformance                                 #
# --------------------------------------------------------------------------- #


class TestProtocolConformance:
    def test_github_source_satisfies_protocol_isinstance(self) -> None:
        impl = GitHubIssueSource(_silent_logger(), gh=FakeGitHubClient())
        assert isinstance(impl, IssueSource)

    def test_prds_source_satisfies_protocol_isinstance(self, tmp_path: Path) -> None:
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        assert isinstance(impl, IssueSource)

    def test_runtime_checkable_rejects_arbitrary_object(self) -> None:
        class NotASource:
            pass

        assert not isinstance(NotASource(), IssueSource)


# --------------------------------------------------------------------------- #
# GitHubIssueSource.preflight                                                 #
# --------------------------------------------------------------------------- #


class TestGitHubPreflight:
    def test_returns_none_when_gh_ok(self) -> None:
        gh = FakeGitHubClient(
            authed=True, repo=gh_module.Repo(owner="x", name="y", default_branch="main")
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        assert impl.preflight() is None

    def test_returns_one_when_gh_not_authed(self) -> None:
        impl = GitHubIssueSource(_silent_logger(), gh=FakeGitHubClient(authed=False))
        assert impl.preflight() == 1

    def test_returns_one_when_auth_status_raises(self) -> None:
        gh = FakeGitHubClient(
            auth_status_error=gh_module.GhError(["gh", "auth", "status"], 127, "missing")
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        assert impl.preflight() == 1

    def test_returns_one_when_repo_view_raises(self) -> None:
        gh = FakeGitHubClient(
            authed=True,
            repo_view_error=gh_module.GhError(["gh", "repo", "view"], 1, "not a repo"),
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        assert impl.preflight() == 1


# --------------------------------------------------------------------------- #
# GitHubIssueSource.collect_pool                                              #
# --------------------------------------------------------------------------- #


class TestGitHubCollectPool:
    def test_returns_empty_when_list_raises(self) -> None:
        gh = FakeGitHubClient(
            issue_list_error=gh_module.GhError(["gh", "issue", "list"], 1, "boom")
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        assert impl.collect_pool().items == ()

    def test_a_failed_list_is_an_incomplete_collection_not_an_empty_pool(
        self,
    ) -> None:
        """#219 §2.13: a failed read may never establish an empty **Pool**.

        The bare empty collection above is byte-identical to the one a
        genuinely empty tracker produces, so a caller that terminates a Run on
        an empty Pool cannot tell "there is no work" from "I could not look" —
        which is exactly what :attr:`PoolCollection.complete` answers.
        """
        gh = FakeGitHubClient(
            issue_list_error=gh_module.GhError(["gh", "issue", "list"], 1, "boom")
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        assert impl.collect_pool().complete is False

    def test_returns_the_pool_in_selection_order(self) -> None:
        """A serial **Pickup** takes ``items[0]``, so the read decides sequence.

        The same rule ``shallow_membership`` already obeys, applied to the
        collection a serial **Iteration** works from (#394). Ordering here rather
        than at the Pickup keeps "which issue is next" a property of the
        snapshot, so the prompt, the completion whitelist and the binding all
        read one sequence.
        """
        gh = FakeGitHubClient(
            issues=[
                _make_issue(31, created_at="2026-05-01T00:00:00Z"),
                _make_issue(7, created_at="2026-01-01T00:00:00Z"),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        assert [i.ref for i in impl.collect_pool().items] == [7, 31]

    def test_a_priority_item_heads_the_collected_pool(self) -> None:
        gh = FakeGitHubClient(
            issues=[
                _make_issue(7, created_at="2026-01-01T00:00:00Z"),
                _make_issue(
                    31,
                    created_at="2026-05-01T00:00:00Z",
                    labels=["ready-for-agent", "priority"],
                ),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        assert [i.ref for i in impl.collect_pool().items] == [31, 7]

    def test_an_undated_item_is_reported_and_sorts_last(self) -> None:
        gh = FakeGitHubClient(
            issues=[
                _make_issue(31, created_at="not-a-timestamp"),
                _make_issue(7, created_at="2026-01-01T00:00:00Z"),
            ]
        )
        logger = _silent_logger()
        impl = GitHubIssueSource(logger, gh=gh)

        with _capture(logger) as records:
            assert [i.ref for i in impl.collect_pool().items] == [7, 31]

        assert any(
            "#31" in record.getMessage() and "malformed" in record.getMessage()
            for record in records
        ), [r.getMessage() for r in records]

    def test_the_order_is_decided_before_the_authoritative_read(self) -> None:
        """The N+1 view loop walks §3.2 order, so a truncated pass is a prefix.

        An implementation that viewed in ``gh``'s listing order and sorted the
        results afterwards would look identical here — until a view failed, at
        which point it would have paid for the newest issues and dropped an
        older one it had never read.
        """
        gh = FakeGitHubClient(
            issues=[
                _make_issue(31, created_at="2026-05-01T00:00:00Z"),
                _make_issue(7, created_at="2026-01-01T00:00:00Z"),
                _make_issue(12, created_at="2026-03-01T00:00:00Z"),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        impl.collect_pool()

        assert gh.issue_view_calls == [7, 12, 31]

    def test_filters_out_issues_lacking_discriminator(self) -> None:
        good = _make_issue(42)
        bad = _make_issue(43, body="just words, no sections")
        gh = FakeGitHubClient(issues=[good, bad])

        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        items = list(impl.collect_pool().items)

        assert [i.ref for i in items] == [42]
        # Discriminator filter runs BEFORE the per-issue view to save
        # the N+1 round-trip on non-AFK-ready candidates.
        assert gh.issue_view_calls == [42], (
            f"expected only #42 to be view-fetched; got {gh.issue_view_calls}"
        )

    def test_renders_block_with_header_body_and_no_comments(self) -> None:
        issue = _make_issue(
            42,
            title="Do the thing",
            labels=["ready-for-agent", "bug"],
            body="## Parent\n#1\n\n## What to build\nthing\n\n## Acceptance criteria\n- ok",
        )
        impl = GitHubIssueSource(_silent_logger(), gh=FakeGitHubClient(issues=[issue]))
        items = list(impl.collect_pool().items)
        assert len(items) == 1
        block = items[0].rendered_block
        assert block.startswith(
            "=== Issue #42: Do the thing [labels: ready-for-agent, bug] ==="
        )
        assert "## What to build" in block
        assert "## Acceptance criteria" in block

    def test_item_carries_issue_labels_for_parallel_eligibility(self) -> None:
        """The item exposes the issue's labels so Parallel mode (#61, ADR-0008)

        can read a human's ``parallel-safe`` assertion off the pool without a
        second round-trip. Eligibility is never inferred — it is a label.
        """
        issue = _make_issue(42, labels=["ready-for-agent", "parallel-safe"])
        impl = GitHubIssueSource(_silent_logger(), gh=FakeGitHubClient(issues=[issue]))
        items = list(impl.collect_pool().items)
        assert len(items) == 1
        assert items[0].labels == ("ready-for-agent", "parallel-safe")
        assert "parallel-safe" in items[0].labels

    def test_renders_block_with_recent_comments_newest_first(self) -> None:
        comments = (
            gh_module.Comment(
                author="alice", body="old comment", created_at="2026-05-10T00:00:00Z"
            ),
            gh_module.Comment(
                author="bob", body="newer comment", created_at="2026-05-15T00:00:00Z"
            ),
        )
        issue = _make_issue(42, comments=comments)
        impl = GitHubIssueSource(_silent_logger(), gh=FakeGitHubClient(issues=[issue]))
        items = list(impl.collect_pool().items)
        block = items[0].rendered_block

        # Newest comment should appear first in the block (after the
        # "--- Recent comments" separator).
        assert "--- Recent comments (newest first, up to 5) ---" in block
        comments_section = block.split(
            "--- Recent comments (newest first, up to 5) ---"
        )[1]
        bob_pos = comments_section.index("@bob")
        alice_pos = comments_section.index("@alice")
        assert bob_pos < alice_pos, (
            "newest comment (bob) should appear before older (alice)"
        )

    def test_skips_issue_view_failure_continues_others(self) -> None:
        ok = _make_issue(42)
        broken = _make_issue(99)
        # The list yields both, but the per-issue view fails for #99 only — the
        # source must skip it and keep #42.
        gh = FakeGitHubClient(
            issues=[ok, broken],
            issue_view_errors={99: gh_module.GhError(["gh"], 1, "broken")},
        )

        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        items = list(impl.collect_pool().items)
        assert [i.ref for i in items] == [42]

    def test_an_unreadable_candidate_makes_the_collection_incomplete(self) -> None:
        """A skipped candidate is a *partial* Pool, not a smaller one (#219 §2.13).

        The candidate above is neither an item nor an exclusion — it was never
        discriminated against — so a caller reading only ``items`` and
        ``exclusions`` sees a collection that looks whole. It is not: #99 is
        still open, still ``ready-for-agent``, and still eligible.
        """
        gh = FakeGitHubClient(
            issues=[_make_issue(42), _make_issue(99)],
            issue_view_errors={99: gh_module.GhError(["gh"], 1, "broken")},
        )

        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        assert impl.collect_pool().complete is False

    def test_a_wholly_readable_pool_is_complete(self) -> None:
        """Non-vacuity: an excluded candidate is a *decision*, not a gap.

        Only a candidate the source could not read leaves the Pool partial. One
        the discriminator judged and dropped was seen, so the collection still
        saw the whole Pool.
        """
        gh = FakeGitHubClient(
            issues=[_make_issue(42), _make_issue(43, body="just words")]
        )

        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        collection = impl.collect_pool()

        assert [e.ref for e in collection.exclusions] == [43]
        assert collection.complete is True

    def test_re_verifies_discriminator_on_full_body(self) -> None:
        """If issue_view returns a different body lacking the discriminator, drop it."""
        # The list body carries the discriminator; the full single view does not,
        # so the source must drop it at the re-verify step (not at the list step).
        gh = FakeGitHubClient(
            issues=[_make_issue(42)],
            issue_views={42: _make_issue(42, body="No discriminator anymore")},
        )

        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        assert impl.collect_pool().items == ()


# --------------------------------------------------------------------------- #
# GitHubIssueSource.collect_pool — Pool exclusions (issue #303)               #
# --------------------------------------------------------------------------- #


class TestGitHubCollectPoolExclusions:
    """A ``ready-for-agent`` candidate the discriminator drops is reported.

    A human deliberately triaged these issues; before #303 they left the
    **Pool** with no diagnostic at all, so the runner looked like it was
    ignoring triage.
    """

    def test_records_the_excluded_candidate_with_its_reason(self) -> None:
        good = _make_issue(42)
        bad = _make_issue(43, title="PRD: something", body="## Acceptance criteria\n- x")
        impl = GitHubIssueSource(
            _silent_logger(), gh=FakeGitHubClient(issues=[good, bad])
        )

        collection = impl.collect_pool()

        assert [i.ref for i in collection.items] == [42]
        assert len(collection.exclusions) == 1
        exclusion = collection.exclusions[0]
        assert exclusion.ref == 43
        assert exclusion.title == "PRD: something"
        assert exclusion.reason == sources_module.EXCLUSION_MISSING_WHAT_TO_BUILD

    def test_records_every_excluded_candidate_in_source_order(self) -> None:
        impl = GitHubIssueSource(
            _silent_logger(),
            gh=FakeGitHubClient(
                issues=[
                    _make_issue(10, body="## What to build\nthing"),
                    _make_issue(11, body="nothing at all"),
                    _make_issue(12),
                ]
            ),
        )

        collection = impl.collect_pool()

        assert [e.ref for e in collection.exclusions] == [10, 11]
        assert [e.reason for e in collection.exclusions] == [
            sources_module.EXCLUSION_MISSING_ACCEPTANCE_CRITERIA,
            sources_module.EXCLUSION_MISSING_BOTH_SECTIONS,
        ]

    def test_no_exclusions_when_every_candidate_is_afk_ready(self) -> None:
        impl = GitHubIssueSource(
            _silent_logger(), gh=FakeGitHubClient(issues=[_make_issue(1)])
        )
        assert impl.collect_pool().exclusions == ()

    def test_records_a_candidate_excluded_on_re_verification(self) -> None:
        """A body that loses the shape between list and view is still reported.

        The re-verify step is the second place a candidate can silently leave
        the Pool, and it drops the *authoritative* body — so it must report the
        authoritative reason rather than the cheaper list body's.
        """
        gh = FakeGitHubClient(
            issues=[_make_issue(42)],
            issue_views={42: _make_issue(42, body="## What to build\nonly this")},
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        collection = impl.collect_pool()

        assert collection.items == ()
        assert [e.ref for e in collection.exclusions] == [42]
        assert (
            collection.exclusions[0].reason
            == sources_module.EXCLUSION_MISSING_ACCEPTANCE_CRITERIA
        )

    def test_a_failed_issue_view_is_not_reported_as_an_exclusion(self) -> None:
        """An unreadable candidate was not *discriminated* against.

        Reporting it as an exclusion would tell the operator to fix headings
        that are probably fine; the existing warn-and-skip path already covers
        a transient read failure.
        """
        gh = FakeGitHubClient(
            issues=[_make_issue(42), _make_issue(99)],
            issue_view_errors={99: gh_module.GhError(["gh"], 1, "broken")},
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        collection = impl.collect_pool()

        assert [i.ref for i in collection.items] == [42]
        assert collection.exclusions == ()

    def test_a_failed_list_reports_no_exclusions(self) -> None:
        """A failed list saw no candidates, so it excluded none of them."""
        gh = FakeGitHubClient(
            issue_list_error=gh_module.GhError(["gh", "issue", "list"], 1, "boom")
        )
        collection = GitHubIssueSource(_silent_logger(), gh=gh).collect_pool()
        assert collection.items == ()
        assert collection.exclusions == ()

    def test_collect_pool_never_views_an_excluded_candidate(self) -> None:
        """Reporting the exclusion must not re-introduce the N+1 round trip."""
        gh = FakeGitHubClient(
            issues=[_make_issue(42), _make_issue(43, body="no sections")]
        )
        GitHubIssueSource(_silent_logger(), gh=gh).collect_pool()
        assert gh.issue_view_calls == [42]


# --------------------------------------------------------------------------- #
# PoolCollection / PoolExclusion shape (issue #303)                           #
# --------------------------------------------------------------------------- #


class TestPoolCollectionShape:
    def test_pool_exclusion_is_frozen(self) -> None:
        exclusion = sources_module.PoolExclusion(
            ref=1, title="t", reason=sources_module.EXCLUSION_MISSING_BOTH_SECTIONS
        )
        with pytest.raises(Exception):
            exclusion.ref = 2  # type: ignore[misc]

    def test_pool_collection_defaults_to_empty(self) -> None:
        collection = sources_module.PoolCollection()
        assert collection.items == ()
        assert collection.exclusions == ()

    def test_excluded_only_is_true_when_everything_was_dropped(self) -> None:
        collection = sources_module.PoolCollection(
            items=(),
            exclusions=(
                sources_module.PoolExclusion(
                    ref=7,
                    title="t",
                    reason=sources_module.EXCLUSION_MISSING_BOTH_SECTIONS,
                ),
            ),
        )
        assert collection.excluded_only is True

    def test_excluded_only_is_false_for_a_genuinely_empty_pool(self) -> None:
        # No ready-for-agent work at all is a different operator situation
        # from work that exists but was dropped for its shape.
        assert sources_module.PoolCollection().excluded_only is False

    def test_excluded_only_is_false_when_work_survived(self) -> None:
        collection = sources_module.PoolCollection(
            items=(AfkReadyItem(ref=1, title="t", rendered_block="x"),),
            exclusions=(
                sources_module.PoolExclusion(
                    ref=7,
                    title="t",
                    reason=sources_module.EXCLUSION_MISSING_BOTH_SECTIONS,
                ),
            ),
        )
        assert collection.excluded_only is False


# --------------------------------------------------------------------------- #
# GitHubIssueSource.handle_completions                                        #
# --------------------------------------------------------------------------- #


class TestGitHubHandleCompletions:
    def test_returns_empty_when_no_new_commits(self) -> None:
        impl = GitHubIssueSource(_silent_logger(), gh=FakeGitHubClient())
        completions = impl.handle_completions(
            pool=[AfkReadyItem(ref=42, title="t", rendered_block="x")],
            new_commits=[],
        )
        assert completions == []

    def test_returns_empty_when_pool_is_empty(self) -> None:
        impl = GitHubIssueSource(_silent_logger(), gh=FakeGitHubClient())
        completions = impl.handle_completions(
            pool=[],
            new_commits=[_FakeCommit("sha1", "Closes #42")],
        )
        assert completions == []

    def test_closes_issue_when_commit_references_pool_member(self) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(42)])
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        completions = impl.handle_completions(
            pool=[AfkReadyItem(ref=42, title="t", rendered_block="x")],
            new_commits=[
                _FakeCommit("sha_abc", "feat: impl", body="Closes #42")
            ],
        )

        assert len(completions) == 1
        assert completions[0].ref == 42
        assert completions[0].sha == "sha_abc"
        assert completions[0].shas == ("sha_abc",)
        assert len(gh.issue_close_calls) == 1
        assert gh.issue_close_calls[0][0] == 42

    def test_skips_close_when_ref_not_in_pool(self) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(42)])
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        completions = impl.handle_completions(
            pool=[AfkReadyItem(ref=42, title="t", rendered_block="x")],
            new_commits=[_FakeCommit("sha", "Closes #99")],
        )
        assert completions == []
        assert gh.issue_close_calls == []

    def test_skips_close_when_issue_already_closed(self) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(42, state="CLOSED")])
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        completions = impl.handle_completions(
            pool=[AfkReadyItem(ref=42, title="t", rendered_block="x")],
            new_commits=[_FakeCommit("sha", "Closes #42")],
        )
        assert completions == []
        assert gh.issue_close_calls == []

    def test_close_failure_is_non_fatal_to_other_completions(self) -> None:
        # The close of #42 fails; #43 must still be closed and completed.
        gh = FakeGitHubClient(
            issues=[_make_issue(42), _make_issue(43)],
            issue_close_errors={42: gh_module.GhError(["gh"], 1, "boom")},
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        completions = impl.handle_completions(
            pool=[
                AfkReadyItem(ref=42, title="t", rendered_block="x"),
                AfkReadyItem(ref=43, title="t", rendered_block="x"),
            ],
            new_commits=[
                _FakeCommit("sha1", "Closes #42"),
                _FakeCommit("sha2", "Fixes #43"),
            ],
        )
        # #42 close raised → no completion; #43 still proceeds.
        assert [c.ref for c in completions] == [43]
        assert [num for num, _ in gh.issue_close_calls] == [42, 43]

    def test_attributes_multiple_shas_when_multiple_commits_reference_issue(
        self,
    ) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(42)])
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        completions = impl.handle_completions(
            pool=[AfkReadyItem(ref=42, title="t", rendered_block="x")],
            new_commits=[
                _FakeCommit("sha_a", "first half", body="Refs #42"),
                _FakeCommit("sha_b", "complete", body="Closes #42"),
                _FakeCommit("sha_c", "follow-up", body="Fixes #42"),
            ],
        )
        # Only sha_b and sha_c contain CLOSING keywords (Refs isn't one);
        # extract_close_refs returns 42 (deduped). Both closing commits
        # should be attributed.
        assert len(completions) == 1
        assert completions[0].ref == 42
        assert set(completions[0].shas) == {"sha_b", "sha_c"}


# --------------------------------------------------------------------------- #
# GitHubIssueSource.comment / PrdsIssueSource.comment  (#63 breadcrumb seam)   #
# --------------------------------------------------------------------------- #


class TestGitHubComment:
    def test_delegates_an_int_ref_to_gh_issue_comment(self) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(42)])
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        impl.comment(42, "auto-resolution exhausted; falling back to serial")

        assert gh.issue_comment_calls == [
            (42, "auto-resolution exhausted; falling back to serial")
        ]
        # A comment resolves nothing — the issue stays OPEN for the serial round.
        assert gh.issue_close_calls == []

    def test_ignores_a_non_int_ref(self) -> None:
        gh = FakeGitHubClient()
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        impl.comment("PRD-7", "note")

        assert gh.issue_comment_calls == []

    def test_comment_failure_is_swallowed(self) -> None:
        # A failed breadcrumb must not propagate — the fallback proceeds
        # without the note rather than aborting the Wave barrier.
        gh = FakeGitHubClient(
            issues=[_make_issue(42)],
            issue_comment_errors={42: gh_module.GhError(["gh"], 1, "boom")},
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        impl.comment(42, "note")  # must not raise

        assert gh.issue_comment_calls == [(42, "note")]


class TestPrdsComment:
    def test_is_a_no_op(self, tmp_path: Path) -> None:
        # The local markdown backend never runs Integration recovery, so a
        # breadcrumb has nowhere to go — the call is a silent no-op.
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        impl.comment(42, "note")  # must not raise





class TestPrdsPreflight:
    def test_returns_none_even_when_prds_dir_missing(
        self, tmp_path: Path
    ) -> None:
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        assert impl.preflight() is None

    def test_returns_none_when_prds_dir_exists(self, tmp_path: Path) -> None:
        (tmp_path / "prds").mkdir()
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        assert impl.preflight() is None


# --------------------------------------------------------------------------- #
# PrdsIssueSource.collect_pool                                                #
# --------------------------------------------------------------------------- #


_AFK_BODY = "## Parent\n#1\n\n## What to build\nthing\n\n## Acceptance criteria\n- a"
_NON_AFK_BODY = "Just a regular body without sections."


def _write_md(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class TestPrdsCollectPool:
    def test_returns_empty_when_no_prds_dir(self, tmp_path: Path) -> None:
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        assert impl.collect_pool().items == ()

    def test_returns_empty_when_prds_dir_empty(self, tmp_path: Path) -> None:
        (tmp_path / "prds").mkdir()
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        assert impl.collect_pool().items == ()

    def test_discovers_single_nnn_file_with_discriminator(
        self, tmp_path: Path
    ) -> None:
        _write_md(tmp_path / "prds" / "featA" / "001-foo.md", _AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        items = list(impl.collect_pool().items)
        assert len(items) == 1
        assert items[0].ref == "prds/featA/001-foo.md"
        assert items[0].title == "prds/featA/001-foo.md"
        assert items[0].rendered_block.startswith("=== prds/featA/001-foo.md ===\n")
        assert "## Parent" in items[0].rendered_block

    def test_skips_prd_md(self, tmp_path: Path) -> None:
        _write_md(tmp_path / "prds" / "featA" / "prd.md", _AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        assert impl.collect_pool().items == ()

    def test_skips_files_without_nnn_prefix(self, tmp_path: Path) -> None:
        _write_md(tmp_path / "prds" / "featA" / "notes.md", _AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "001-real.md", _AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        items = list(impl.collect_pool().items)
        assert [i.ref for i in items] == ["prds/featA/001-real.md"]

    def test_skips_files_lacking_afk_discriminator(self, tmp_path: Path) -> None:
        _write_md(tmp_path / "prds" / "featA" / "001-incomplete.md", _NON_AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "002-ready.md", _AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        items = list(impl.collect_pool().items)
        assert [i.ref for i in items] == ["prds/featA/002-ready.md"]

    def test_skips_done_subdirectory_files(self, tmp_path: Path) -> None:
        _write_md(tmp_path / "prds" / "featA" / "done" / "001-archived.md", _AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "002-active.md", _AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        items = list(impl.collect_pool().items)
        assert [i.ref for i in items] == ["prds/featA/002-active.md"]

    # -- Pool exclusions (issue #303) ------------------------------------- #

    def test_reports_a_dropped_file_with_its_reason(self, tmp_path: Path) -> None:
        """The shared discriminator means the shared exclusion vocabulary."""
        _write_md(tmp_path / "prds" / "featA" / "001-incomplete.md", _NON_AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "002-ready.md", _AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())

        collection = impl.collect_pool()

        assert [i.ref for i in collection.items] == ["prds/featA/002-ready.md"]
        assert [e.ref for e in collection.exclusions] == [
            "prds/featA/001-incomplete.md"
        ]
        assert (
            collection.exclusions[0].reason
            == sources_module.EXCLUSION_MISSING_BOTH_SECTIONS
        )
        assert collection.exclusions[0].title == "prds/featA/001-incomplete.md"

    def test_orders_exclusions_by_repo_relative_path(self, tmp_path: Path) -> None:
        _write_md(tmp_path / "prds" / "featB" / "001-b.md", _NON_AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "001-a.md", "## What to build\nx")
        impl = PrdsIssueSource(tmp_path, _silent_logger())

        collection = impl.collect_pool()

        assert [e.ref for e in collection.exclusions] == [
            "prds/featA/001-a.md",
            "prds/featB/001-b.md",
        ]
        assert collection.exclusions[0].reason == (
            sources_module.EXCLUSION_MISSING_ACCEPTANCE_CRITERIA
        )

    def test_a_non_candidate_file_is_not_an_exclusion(self, tmp_path: Path) -> None:
        """``prd.md`` and un-numbered notes never entered the discriminator."""
        _write_md(tmp_path / "prds" / "featA" / "prd.md", _NON_AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "notes.md", _NON_AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "001-ready.md", _AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())

        assert impl.collect_pool().exclusions == ()

    def test_an_archived_file_is_not_an_exclusion(self, tmp_path: Path) -> None:
        _write_md(tmp_path / "prds" / "featA" / "done" / "001-old.md", _NON_AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        assert impl.collect_pool().exclusions == ()

    def test_orders_within_feature_numerically_by_nnn(
        self, tmp_path: Path
    ) -> None:
        # Zero-padded NNN sorts lex-equivalent-to-numerical, matching
        # POSIX `find ... | sort` ordering.
        _write_md(tmp_path / "prds" / "featA" / "003-c.md", _AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "001-a.md", _AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "002-b.md", _AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        items = list(impl.collect_pool().items)
        assert [i.ref for i in items] == [
            "prds/featA/001-a.md",
            "prds/featA/002-b.md",
            "prds/featA/003-c.md",
        ]

    def test_orders_across_features_lexicographically(
        self, tmp_path: Path
    ) -> None:
        _write_md(tmp_path / "prds" / "alpha" / "001-a.md", _AFK_BODY)
        _write_md(
            tmp_path / "prds" / "alpha-beta" / "001-ab.md", _AFK_BODY
        )
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        items = list(impl.collect_pool().items)
        assert [i.ref for i in items] == [
            "prds/alpha-beta/001-ab.md",
            "prds/alpha/001-a.md",
        ]

    def test_multi_feature_multi_file_full_ordering(
        self, tmp_path: Path
    ) -> None:
        # 5 files across 2 features; verify full deterministic order.
        _write_md(tmp_path / "prds" / "featA" / "001-a.md", _AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "010-aa.md", _AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "002-b.md", _AFK_BODY)
        _write_md(tmp_path / "prds" / "featB" / "005-b5.md", _AFK_BODY)
        _write_md(tmp_path / "prds" / "featB" / "001-b1.md", _AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        items = list(impl.collect_pool().items)
        assert [i.ref for i in items] == [
            "prds/featA/001-a.md",
            "prds/featA/002-b.md",
            "prds/featA/010-aa.md",
            "prds/featB/001-b1.md",
            "prds/featB/005-b5.md",
        ]

    def test_skips_top_level_done_directory(self, tmp_path: Path) -> None:
        # Defensive: a top-level `prds/done/` shouldn't be a feature dir
        # but if it exists we don't iterate it.
        _write_md(tmp_path / "prds" / "done" / "001-archived.md", _AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "001-active.md", _AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        items = list(impl.collect_pool().items)
        assert [i.ref for i in items] == ["prds/featA/001-active.md"]

    def test_skips_loose_md_files_at_top_level(self, tmp_path: Path) -> None:
        # `prds/README.md` isn't inside a feature dir; should be ignored.
        _write_md(tmp_path / "prds" / "README.md", _AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "001-a.md", _AFK_BODY)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        items = list(impl.collect_pool().items)
        assert [i.ref for i in items] == ["prds/featA/001-a.md"]

    def test_skips_symlinked_feature_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "outside-feature"
        _write_md(target / "001-escaped.md", _AFK_BODY)
        linked = tmp_path / "prds" / "escaped"
        linked.parent.mkdir(parents=True)
        try:
            linked.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"directory symlinks are unavailable: {exc}")
        _write_md(tmp_path / "prds" / "featA" / "002-active.md", _AFK_BODY)

        impl = PrdsIssueSource(tmp_path, _silent_logger())

        assert [i.ref for i in list(impl.collect_pool().items)] == [
            "prds/featA/002-active.md"
        ]

    def test_skips_symlinked_issue_files(self, tmp_path: Path) -> None:
        target = tmp_path / "outside-issue.md"
        _write_md(target, _AFK_BODY)
        linked = tmp_path / "prds" / "featA" / "001-escaped.md"
        linked.parent.mkdir(parents=True)
        try:
            linked.symlink_to(target)
        except OSError as exc:
            pytest.skip(f"file symlinks are unavailable: {exc}")
        _write_md(tmp_path / "prds" / "featA" / "002-active.md", _AFK_BODY)

        impl = PrdsIssueSource(tmp_path, _silent_logger())

        assert [i.ref for i in list(impl.collect_pool().items)] == [
            "prds/featA/002-active.md"
        ]

    def test_skips_symlinked_prds_root(self, tmp_path: Path) -> None:
        target = tmp_path / "outside-prds"
        _write_md(target / "feature" / "001-escaped.md", _AFK_BODY)
        linked = tmp_path / "repo" / "prds"
        linked.parent.mkdir(parents=True)
        try:
            linked.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"directory symlinks are unavailable: {exc}")

        impl = PrdsIssueSource(linked.parent, _silent_logger())

        assert impl.collect_pool().items == ()

    def test_a_refused_prds_root_is_incomplete_not_an_empty_pool(
        self, tmp_path: Path
    ) -> None:
        """A ``prds/`` the source declined to walk is unseen, not absent.

        Same rule the GitHub backend applies (#219 §2.13): only a Pool the
        source actually saw may claim to be empty. A missing ``prds/`` really
        is empty; one it refused to follow is not.
        """
        target = tmp_path / "outside-prds"
        _write_md(target / "feature" / "001-escaped.md", _AFK_BODY)
        linked = tmp_path / "repo" / "prds"
        linked.parent.mkdir(parents=True)
        try:
            linked.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"directory symlinks are unavailable: {exc}")

        impl = PrdsIssueSource(linked.parent, _silent_logger())

        assert impl.collect_pool().complete is False

    def test_an_absent_prds_directory_is_a_complete_empty_pool(
        self, tmp_path: Path
    ) -> None:
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        collection = impl.collect_pool()

        assert collection.items == ()
        assert collection.complete is True

    def test_rendered_block_format_matches_bash_collector(
        self, tmp_path: Path
    ) -> None:
        # The block is "=== <path> ===\n<file contents>".
        body = "## Parent\n#1\n\n## What to build\nthing\n\n## Acceptance criteria\n- ok\n"
        _write_md(tmp_path / "prds" / "featA" / "001-a.md", body)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        items = list(impl.collect_pool().items)
        assert items[0].rendered_block == f"=== prds/featA/001-a.md ===\n{body}"

    def test_unreadable_file_is_skipped_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate a read failure on one file but success on another.
        _write_md(tmp_path / "prds" / "featA" / "001-good.md", _AFK_BODY)
        _write_md(tmp_path / "prds" / "featA" / "002-bad.md", _AFK_BODY)

        real_read = Path.read_text

        def fake_read(self: Path, *args: Any, **kwargs: Any) -> str:
            if self.name == "002-bad.md":
                raise OSError("simulated read failure")
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fake_read)
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        items = list(impl.collect_pool().items)
        assert [i.ref for i in items] == ["prds/featA/001-good.md"]


# --------------------------------------------------------------------------- #
# PrdsIssueSource.handle_completions                                          #
# --------------------------------------------------------------------------- #


class TestPrdsHandleCompletions:
    def test_returns_empty_with_no_pool(self, tmp_path: Path) -> None:
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        assert impl.handle_completions(pool=[], new_commits=[]) == []

    def test_returns_empty_even_when_commit_references_file(
        self, tmp_path: Path
    ) -> None:
        """The wrapper does NOT auto-move files.

        Even if a new commit's message literally contains the pool file
        path, ``handle_completions`` returns an empty list. The agent
        owns the ``git mv`` step; the wrapper only discovers the
        resulting state on the next iteration.
        """
        _write_md(tmp_path / "prds" / "featA" / "001-a.md", _AFK_BODY)
        pool = [
            AfkReadyItem(
                ref="prds/featA/001-a.md", title="x", rendered_block="x"
            )
        ]
        commits = [_FakeCommit("sha", "git mv prds/featA/001-a.md prds/featA/done/")]
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        assert impl.handle_completions(pool=pool, new_commits=commits) == []

    def test_does_not_mutate_filesystem(self, tmp_path: Path) -> None:
        """Critical invariant: handle_completions must leave the worktree clean.

        A wrapper-side move would dirty the tree; under ADR-0004 the runner
        Checkpoint would now capture that rather than abort, but detection-only
        keeps the PRDs closure attributable to the agent's own ``git mv``
        commit instead of an anonymous Checkpoint.
        """
        original = tmp_path / "prds" / "featA" / "001-a.md"
        _write_md(original, _AFK_BODY)
        before_files = {
            p for p in (tmp_path / "prds").rglob("*") if p.is_file()
        }
        pool = [
            AfkReadyItem(
                ref="prds/featA/001-a.md", title="x", rendered_block="x"
            )
        ]
        commits = [_FakeCommit("sha", "Closes prds/featA/001-a.md")]

        impl = PrdsIssueSource(tmp_path, _silent_logger())
        impl.handle_completions(pool=pool, new_commits=commits)

        after_files = {
            p for p in (tmp_path / "prds").rglob("*") if p.is_file()
        }
        assert before_files == after_files, (
            "PrdsIssueSource.handle_completions must NOT mutate the filesystem"
        )
        assert original.exists(), "the pool file must still exist after handle_completions"


# --------------------------------------------------------------------------- #
# Module-level structure / imports                                            #
# --------------------------------------------------------------------------- #


class TestModuleStructure:
    def test_exports_documented_public_surface(self) -> None:
        expected = {
            "AfkReadyItem",
            "Completion",
            "IssueSource",
            "GitHubIssueSource",
            "MembershipSnapshot",
            "Pickup",
            "PoolCandidate",
            "PrdsIssueSource",
            "RollingIssueSource",
            "LABEL_PARALLEL_SAFE",
            "LABEL_READY_FOR_AGENT",
            "PICKUP_STALE",
            "PICKUP_UNAVAILABLE",
            "PICKUP_VALIDATED",
            "EXCLUSION_MISSING_ACCEPTANCE_CRITERIA",
            "EXCLUSION_MISSING_BOTH_SECTIONS",
            "EXCLUSION_MISSING_WHAT_TO_BUILD",
            "EXCLUSION_REASONS",
            "PoolCollection",
            "PoolExclusion",
            "afk_ready_exclusion",
            "in_selection_order",
            "is_afk_ready",
            "is_pr_afk_ready",
        }
        assert set(sources_module.__all__) == expected
        for name in expected:
            assert hasattr(sources_module, name)

    def test_protocol_is_runtime_checkable(self) -> None:
        # The Protocol must remain @runtime_checkable so the loop and
        # tests can confirm structural conformance via isinstance.
        from typing import _ProtocolMeta  # type: ignore[attr-defined]

        assert isinstance(IssueSource, _ProtocolMeta)

    def test_imports_are_constrained(self) -> None:
        """sources.py may only import stdlib + git_loopy.{gh,git,issue_order,issue_pin,wrapper}.

        Forbidden: copilot SDK, rich, git_loopy.{loop,cli,config,session,
        ui,persist,events,pricing,telemetry} — keeps the Protocol seam
        light and the unit-test surface fast.

        ``issue_order`` joined the allowlist with #393, when a **Pool** read
        started deciding sequence. It is the one module that may: §3.2's order
        is a Wrapper-contract decision pinned across three Orchestrators by
        ``issue-ordering.json``, so a source that sorted for itself would be a
        second implementation of a decision the fixture exists to keep single.
        It costs the seam nothing —
        :meth:`test_the_ordering_seam_stays_stdlib_only` is what keeps that
        true.

        ``issue_pin`` joined it with #396 on identical terms and for the same
        reason: refusing an ineligible ``--issue N`` is one decision three
        Orchestrators share, and a source that decided it inline would be a
        second copy of it. It is held to the same purity guard.
        """
        import ast

        source_path = Path(sources_module.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        allowed_third_party_prefixes: set[str] = set()  # no third-party allowed
        allowed_git_loopy_submodules = {
            "gh",
            "git",
            "issue_order",
            "issue_pin",
            "wrapper",
        }

        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top == "git_loopy":
                        sub = alias.name.split(".", 2)[1] if "." in alias.name else None
                        if sub and sub not in allowed_git_loopy_submodules:
                            offenders.append(
                                f"line {node.lineno}: import {alias.name}"
                            )
                    elif top in {"copilot", "rich"}:
                        offenders.append(f"line {node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    continue
                if node.module is None:
                    continue
                top = node.module.split(".")[0]
                if top == "git_loopy":
                    parts = node.module.split(".")
                    if len(parts) >= 2 and parts[1] not in allowed_git_loopy_submodules:
                        offenders.append(
                            f"line {node.lineno}: from {node.module} import ..."
                        )
                elif top in {"copilot", "rich"}:
                    offenders.append(
                        f"line {node.lineno}: from {node.module} import ..."
                    )
                elif top not in allowed_third_party_prefixes:
                    # stdlib-only is allowed via the default branch
                    # but check for known third-party leaks.
                    if top in {"httpx", "requests", "github", "pygit2"}:
                        offenders.append(
                            f"line {node.lineno}: from {node.module} import ..."
                        )

        assert not offenders, (
            "git-loopy/sources.py has forbidden imports:\n  "
            + "\n  ".join(offenders)
        )

    @pytest.mark.parametrize("module_name", ["issue_order", "issue_pin"])
    def test_the_selection_seams_stay_stdlib_only(self, module_name: str) -> None:
        """Widening the allowlist above must not smuggle a dependency in.

        ``sources.py`` may import ``issue_order`` and ``issue_pin`` because
        ordering and refusing a **Pin** are each one decision that belongs in
        one place. That stays cheap only while those modules themselves import
        nothing from ``git_loopy`` — the moment either reached for ``config``
        or ``events``, the Protocol seam would have acquired the weight this
        class exists to keep off it, transitively and invisibly.

        It is also what forces ``issue_pin`` to be *told* the AFK-ready verdict
        rather than computing it: importing ``sources`` for the discriminator
        would fail here, and would make the pair cyclic besides.
        """
        import ast
        import importlib

        module = importlib.import_module(f"git_loopy.{module_name}")
        tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
        offenders = [
            f"line {node.lineno}: {name}"
            for node in ast.walk(tree)
            for name in (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom) and node.level == 0
                else []
            )
            if name.split(".")[0] in {"git_loopy", "copilot", "rich"}
        ]

        assert not offenders, (
            f"git-loopy/{module_name}.py must stay pure:\n  " + "\n  ".join(offenders)
        )


# Silence pytest's unused-import warning if MagicMock ends up unused.
_ = MagicMock


# --------------------------------------------------------------------------- #
# PR support helpers                                                          #
# --------------------------------------------------------------------------- #


def _make_pr(
    number: int,
    *,
    body: str = "",
    state: str = "OPEN",
    head_sha: str = "a" * 40,
    head_branch: str = "feature/x",
    labels: list[str] | None = None,
    comments: tuple[gh_module.Comment, ...] = (),
) -> gh_module.PullRequest:
    return gh_module.PullRequest(
        number=number,
        title=f"Test PR {number}",
        body=body,
        labels=labels if labels is not None else ["ready-for-agent"],
        state=state,
        url=f"https://github.com/x/y/pull/{number}",
        head_sha=head_sha,
        head_branch=head_branch,
        comments=comments,
    )


def _brief_comment(body: str = "## Agent Brief\nDo the thing.") -> gh_module.Comment:
    return gh_module.Comment(author="triage-bot", body=body, created_at="2026-05-16")


# --------------------------------------------------------------------------- #
# is_pr_afk_ready                                                             #
# --------------------------------------------------------------------------- #


class TestIsPrAfkReady:
    def test_true_when_brief_in_body(self) -> None:
        assert is_pr_afk_ready(_make_pr(7, body="## Agent Brief\nDo X")) is True

    def test_true_when_brief_in_comment(self) -> None:
        pr = _make_pr(7, body="normal description", comments=(_brief_comment(),))
        assert is_pr_afk_ready(pr) is True

    def test_false_when_no_brief_anywhere(self) -> None:
        pr = _make_pr(
            7,
            body="normal",
            comments=(gh_module.Comment("u", "lgtm", "2026-05-16"),),
        )
        assert is_pr_afk_ready(pr) is False

    def test_false_when_brief_not_line_anchored(self) -> None:
        assert is_pr_afk_ready(_make_pr(7, body="see ## Agent Brief inline")) is False

    def test_false_for_empty_pr(self) -> None:
        assert is_pr_afk_ready(_make_pr(7, body="")) is False


# --------------------------------------------------------------------------- #
# PR-aware AfkReadyItem / Completion shape                                    #
# --------------------------------------------------------------------------- #


class TestPrDataclassShapes:
    def test_afk_ready_item_pr_kind_and_head_sha(self) -> None:
        item = AfkReadyItem(
            ref=7, title="t", rendered_block="x", kind="pr", head_sha="abc"
        )
        assert item.kind == "pr"
        assert item.head_sha == "abc"

    def test_afk_ready_item_defaults_to_issue_kind(self) -> None:
        item = AfkReadyItem(ref=1, title="t", rendered_block="x")
        assert item.kind == "issue"
        assert item.head_sha == ""

    def test_completion_defaults_to_issue_kind(self) -> None:
        assert Completion(ref=1, sha="x").kind == "issue"

    def test_completion_pr_kind(self) -> None:
        assert Completion(ref=7, sha="newsha", kind="pr").kind == "pr"


# --------------------------------------------------------------------------- #
# GitHubIssueSource PR collection                                            #
# --------------------------------------------------------------------------- #


class TestGitHubCollectAfkReadyPrs:
    def test_does_not_list_prs_when_include_prs_false(self) -> None:
        # include_prs defaults False, so pr_list must never be called.
        gh = FakeGitHubClient(issues=[], prs=[_make_pr(7)])
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        assert impl.collect_pool().items == ()
        assert gh.pr_list_calls == []

    def test_collects_only_prs_with_brief_when_enabled(self) -> None:
        # #7 carries an agent brief (in a comment); #8 does not — filtered out.
        gh = FakeGitHubClient(
            issues=[],
            prs=[
                _make_pr(7, comments=(_brief_comment(),), head_sha="oldsha"),
                _make_pr(8, body="no brief here"),
            ],
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh, include_prs=True)
        items = list(impl.collect_pool().items)

        assert [i.ref for i in items] == [7]
        assert items[0].kind == "pr"
        assert items[0].head_sha == "oldsha"
        assert items[0].rendered_block.startswith("=== PR #7:")
        assert "(branch: feature/x)" in items[0].rendered_block

    def test_pr_list_failure_is_non_fatal(self) -> None:
        gh = FakeGitHubClient(
            issues=[], pr_list_error=gh_module.GhError(["gh", "pr", "list"], 1, "boom")
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh, include_prs=True)
        assert impl.collect_pool().items == ()


# --------------------------------------------------------------------------- #
# GitHubIssueSource PR-advance detection + mixed-pool backstop               #
# --------------------------------------------------------------------------- #


def _pool_pr(number: int = 7, head_sha: str = "oldsha") -> AfkReadyItem:
    return AfkReadyItem(
        ref=number, title="t", rendered_block="x", kind="pr", head_sha=head_sha
    )


class TestGitHubDetectPrAdvances:
    def test_records_advance_when_head_sha_changed(self) -> None:
        gh = FakeGitHubClient(prs=[_make_pr(7, head_sha="newsha")])
        impl = GitHubIssueSource(_silent_logger(), gh=gh, include_prs=True)
        completions = impl.handle_completions(pool=[_pool_pr()], new_commits=[])
        assert len(completions) == 1
        assert completions[0].ref == 7
        assert completions[0].kind == "pr"
        assert completions[0].sha == "newsha"

    def test_no_advance_when_head_sha_unchanged(self) -> None:
        gh = FakeGitHubClient(prs=[_make_pr(7, head_sha="oldsha")])
        impl = GitHubIssueSource(_silent_logger(), gh=gh, include_prs=True)
        assert (
            impl.handle_completions(pool=[_pool_pr(head_sha="oldsha")], new_commits=[])
            == []
        )

    def test_no_advance_when_pr_merged(self) -> None:
        gh = FakeGitHubClient(prs=[_make_pr(7, head_sha="newsha", state="MERGED")])
        impl = GitHubIssueSource(_silent_logger(), gh=gh, include_prs=True)
        assert impl.handle_completions(pool=[_pool_pr()], new_commits=[]) == []

    def test_detection_skipped_when_include_prs_false(self) -> None:
        # include_prs False → the PR-advance check never runs, so pr_view is untouched.
        gh = FakeGitHubClient(prs=[_make_pr(7, head_sha="newsha")])
        impl = GitHubIssueSource(_silent_logger(), gh=gh)
        assert impl.handle_completions(pool=[_pool_pr()], new_commits=[]) == []
        assert gh.pr_view_calls == []

    def test_pr_view_failure_during_detect_is_non_fatal(self) -> None:
        gh = FakeGitHubClient(
            pr_view_errors={7: gh_module.GhError(["gh", "pr", "view", "7"], 1, "boom")}
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh, include_prs=True)
        assert impl.handle_completions(pool=[_pool_pr()], new_commits=[]) == []


class TestGitHubMixedPoolBackstop:
    def test_closes_keyword_never_closes_pr_sharing_number(self) -> None:
        """A ``Closes #7`` must not ``gh issue close`` #7 when #7 is a PR."""
        # PR-advance check: head unchanged → no PR completion either.
        gh = FakeGitHubClient(
            issues=[_make_issue(7)], prs=[_make_pr(7, head_sha="oldsha")]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh, include_prs=True)
        completions = impl.handle_completions(
            pool=[_pool_pr(number=7, head_sha="oldsha")],
            new_commits=[_FakeCommit("sha", "Closes #7")],
        )
        assert gh.issue_close_calls == []
        assert completions == []

    def test_issue_closure_still_works_with_pr_in_pool(self) -> None:
        gh = FakeGitHubClient(
            issues=[_make_issue(42)], prs=[_make_pr(7, head_sha="oldsha")]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh, include_prs=True)
        completions = impl.handle_completions(
            pool=[
                _pool_pr(number=7, head_sha="oldsha"),
                AfkReadyItem(ref=42, title="t", rendered_block="x"),  # issue
            ],
            new_commits=[_FakeCommit("sha", "Closes #42")],
        )
        assert [num for num, _ in gh.issue_close_calls] == [42]
        assert [c.ref for c in completions] == [42]


# --------------------------------------------------------------------------- #
# Rolling dispatch: shallow membership + targeted pickup (#219 §2)             #
# --------------------------------------------------------------------------- #


class TestShallowMembership:
    """The cheap membership read Rolling dispatch reconciles its cache from."""

    def test_returns_afk_shaped_open_issues_in_selection_order(self) -> None:
        gh = FakeGitHubClient(
            issues=[
                _make_issue(31, labels=["ready-for-agent", "parallel-safe"]),
                _make_issue(7, labels=["ready-for-agent"]),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        snapshot = impl.shallow_membership()

        assert snapshot.complete is True
        # Neither carries a timestamp, so §3.2's tiebreak — the issue number —
        # decides, and `gh`'s newest-first listing stops being the answer.
        assert [c.ref for c in snapshot.candidates] == [7, 31]
        assert snapshot.candidates[1].labels == ("ready-for-agent", "parallel-safe")
        assert snapshot.candidates[1].title == "Test issue 31"

    def test_drops_issues_that_fail_the_afk_shape_discriminator(self) -> None:
        gh = FakeGitHubClient(
            issues=[_make_issue(31), _make_issue(7, body="just a thought")]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        assert [c.ref for c in impl.shallow_membership().candidates] == [31]

    def test_costs_no_per_issue_round_trip(self) -> None:
        """Shallow means shallow: no ``issue_view`` enrichment during refresh."""
        gh = FakeGitHubClient(issues=[_make_issue(31), _make_issue(7)])
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        impl.shallow_membership()

        assert gh.issue_view_calls == []

    def test_failed_read_is_incomplete_rather_than_empty(self) -> None:
        """A failed refresh may never be read as 'the Pool is empty' (#219 §2.13)."""
        gh = FakeGitHubClient(
            issue_list_error=gh_module.GhError(["gh"], 1, "boom")
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        snapshot = impl.shallow_membership()

        assert snapshot.candidates == ()
        assert snapshot.complete is False

    def test_truncated_read_is_incomplete(self) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(31)], issue_list_complete=False)
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        snapshot = impl.shallow_membership()

        assert [c.ref for c in snapshot.candidates] == [31]
        assert snapshot.complete is False

    def test_a_candidate_carries_the_timestamp_the_order_is_built_on(self) -> None:
        """**Rolling dispatch** orders its cache, so the field has to reach the cache.

        A candidate that only acquires ``created_at`` at the authoritative
        pickup read acquires it *after* the decision that needed it — the Lane
        has already been reserved for whatever the cache put at the front.
        """
        gh = FakeGitHubClient(
            issues=[_make_issue(31, created_at="2026-01-02T03:04:05Z")]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        [candidate] = impl.shallow_membership().candidates

        assert candidate.created_at == "2026-01-02T03:04:05Z"

    def test_an_unstamped_candidate_is_undated_rather_than_dropped(self) -> None:
        """§3.2: an undated issue sorts last within its rank; it stays eligible."""
        gh = FakeGitHubClient(issues=[_make_issue(31)])
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        [candidate] = impl.shallow_membership().candidates

        assert candidate.created_at == ""


class TestShallowMembershipSelectionOrder:
    """§3.2 reaches **Rolling dispatch**: the cheap read hands back a Pool in order.

    #219 §2 already walks the candidate cache front to back, so **Parallel
    mode** works whatever order this read produced. Until #393 that was
    ``gh``'s unstated ``sort=created&direction=desc`` — FIFO over a
    newest-first list, which is LIFO.
    """

    def test_candidates_come_back_oldest_first(self) -> None:
        gh = FakeGitHubClient(
            issues=[
                _make_issue(31, created_at="2026-05-01T00:00:00Z"),
                _make_issue(7, created_at="2026-01-01T00:00:00Z"),
                _make_issue(12, created_at="2026-03-01T00:00:00Z"),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        snapshot = impl.shallow_membership()

        assert [c.ref for c in snapshot.candidates] == [7, 12, 31]

    def test_a_priority_candidate_heads_the_order_over_older_ones(self) -> None:
        """**Priority** is a jump to the front of the queue, not out of it."""
        gh = FakeGitHubClient(
            issues=[
                _make_issue(7, created_at="2026-01-01T00:00:00Z"),
                _make_issue(
                    31,
                    created_at="2026-05-01T00:00:00Z",
                    labels=["ready-for-agent", "priority"],
                ),
                _make_issue(12, created_at="2026-03-01T00:00:00Z"),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        assert [c.ref for c in impl.shallow_membership().candidates] == [31, 7, 12]

    def test_the_order_does_not_depend_on_the_order_the_source_listed(self) -> None:
        """§3.2: ordering the same Pool twice yields the same sequence.

        The two clients differ only in the order ``gh`` happened to return, so
        an implementation that leaned on source position at all would show it
        here rather than in a Run three weeks from now.
        """
        oldest = _make_issue(7, created_at="2026-01-01T00:00:00Z")
        newest = _make_issue(31, created_at="2026-05-01T00:00:00Z")
        as_listed = GitHubIssueSource(
            _silent_logger(), gh=FakeGitHubClient(issues=[oldest, newest])
        )
        reversed_listing = GitHubIssueSource(
            _silent_logger(), gh=FakeGitHubClient(issues=[newest, oldest])
        )

        assert [c.ref for c in as_listed.shallow_membership().candidates] == [7, 31]
        assert [
            c.ref for c in reversed_listing.shallow_membership().candidates
        ] == [7, 31]

    def test_identical_timestamps_are_broken_by_issue_number(self) -> None:
        gh = FakeGitHubClient(
            issues=[
                _make_issue(31, created_at="2026-01-01T00:00:00Z"),
                _make_issue(7, created_at="2026-01-01T00:00:00Z"),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        assert [c.ref for c in impl.shallow_membership().candidates] == [7, 31]

    def test_an_undated_candidate_sorts_last_within_its_rank_and_stays(self) -> None:
        """A broken field is not a retracted eligibility assertion."""
        gh = FakeGitHubClient(
            issues=[
                _make_issue(31, created_at="not-a-timestamp"),
                _make_issue(7, created_at="2026-01-01T00:00:00Z"),
                _make_issue(12, created_at=""),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        assert [c.ref for c in impl.shallow_membership().candidates] == [7, 12, 31]

    def test_an_undated_priority_candidate_still_outranks_a_dated_one(self) -> None:
        gh = FakeGitHubClient(
            issues=[
                _make_issue(7, created_at="2026-01-01T00:00:00Z"),
                _make_issue(31, created_at="", labels=["ready-for-agent", "priority"]),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        assert [c.ref for c in impl.shallow_membership().candidates] == [31, 7]

    def test_urgent_sounding_content_does_not_rank_without_the_label(self) -> None:
        """**Priority** is read off the labels, never inferred (#395, §3.2).

        Asked at the *source* seam rather than at the pure comparison, because
        this is where the whole issue record is in hand: ``in_selection_order``
        sees the title, body and comments and maps three fields out of them, so
        a heuristic could be added here without the ordering seam ever noticing.
        Issue 31 is newer and shouts; it still sorts behind the older 7.
        """
        gh = FakeGitHubClient(
            issues=[
                _make_issue(
                    31,
                    title="URGENT: production is down, top priority",
                    body=(
                        "## What to build\nP0, critical, priority\n\n"
                        "## Acceptance criteria\n- now"
                    ),
                    created_at="2026-05-01T00:00:00Z",
                ),
                _make_issue(7, created_at="2026-01-01T00:00:00Z"),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        assert [c.ref for c in impl.shallow_membership().candidates] == [7, 31]

    def test_a_priority_issue_that_is_not_afk_ready_never_enters_the_pool(self) -> None:
        """The head of the order is the head of the *eligible* order (#395).

        Ordering runs over what the discriminator already admitted, so a
        ``priority`` issue with a thought-shaped body is absent rather than
        first. Pinned at this seam because it is the one where the two rules
        meet: an implementation that ordered before filtering would put this
        issue at the front and only then discover it was never a candidate.
        """
        gh = FakeGitHubClient(
            issues=[
                _make_issue(
                    7,
                    body="just a thought",
                    labels=["ready-for-agent", "priority"],
                ),
                _make_issue(31, created_at="2026-05-01T00:00:00Z"),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        assert [c.ref for c in impl.shallow_membership().candidates] == [31]

    def test_an_undated_candidate_is_reported_with_its_defect(self) -> None:
        gh = FakeGitHubClient(
            issues=[
                _make_issue(31, created_at="not-a-timestamp"),
                _make_issue(12, created_at=""),
            ]
        )
        diag = _silent_logger()
        impl = GitHubIssueSource(diag, gh=gh)

        with _capture(diag) as records:
            impl.shallow_membership()

        reported = " ".join(r.getMessage() for r in records)
        assert "31" in reported and "malformed" in reported
        assert "12" in reported and "absent" in reported

    def test_a_persistent_defect_is_reported_once_not_every_refresh(self) -> None:
        """A membership refresh repeats on a backoff; the defect does not move.

        Warning on every refresh would bury the one line that matters under
        the same line, which is how an operator learns to skip it.
        """
        gh = FakeGitHubClient(issues=[_make_issue(31, created_at="")])
        diag = _silent_logger()
        impl = GitHubIssueSource(diag, gh=gh)

        with _capture(diag) as records:
            impl.shallow_membership()
            impl.shallow_membership()
            impl.shallow_membership()

        assert len([r for r in records if "31" in r.getMessage()]) == 1

    def test_an_incomplete_read_is_still_ordered(self) -> None:
        """Ordering is the read's job and does not wait on completeness.

        Whether **Rolling dispatch** then *admits* a partial snapshot is
        #219 §2.12's separate decision — it retains its last complete one — so
        this pins the source's half only: a truncated read is partial, not
        unsorted, and it carries the flag that says which.
        """
        gh = FakeGitHubClient(
            issues=[
                _make_issue(31, created_at="2026-05-01T00:00:00Z"),
                _make_issue(7, created_at="2026-01-01T00:00:00Z"),
            ],
            issue_list_complete=False,
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        snapshot = impl.shallow_membership()

        assert [c.ref for c in snapshot.candidates] == [7, 31]
        assert snapshot.complete is False


class TestPoolFetchCompleteness:
    """Wrapper contract §2: the candidate fetch reaches the end of the backlog.

    Under §3.2's oldest-first order a page limit stops hiding the *oldest*
    issues and starts hiding the *newest*, so a **Priority** issue filed today
    would fall outside the oldest hundred and be invisible exactly when it
    matters most. Completeness is therefore a correctness requirement for
    Priority, not a nicety (#392).
    """

    def test_a_backlog_larger_than_one_page_reaches_the_pool_intact(self) -> None:
        """The true oldest eligible issue is present, and it is the head."""
        issues = [
            _make_issue(n, created_at=f"2026-01-01T00:{n // 60:02d}:{n % 60:02d}Z")
            for n in range(1, 151)
        ]
        gh = FakeGitHubClient(issues=issues)
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        collection = impl.collect_pool()

        assert collection.complete is True
        assert len(collection.items) == 150
        assert collection.items[0].ref == 1
        assert collection.items[-1].ref == 150

    def test_a_priority_issue_past_the_old_page_limit_is_still_fetched(self) -> None:
        """The specific failure oldest-first would have introduced.

        A **Priority** issue filed today is the *newest* row, which is precisely
        what a fixed ``--limit 100`` would have dropped once the order flipped.
        """
        issues = [_make_issue(n, created_at="2025-01-01T00:00:00Z") for n in range(1, 121)]
        issues.append(
            _make_issue(
                999,
                labels=["ready-for-agent", "priority"],
                created_at="2026-08-13T00:00:00Z",
            )
        )
        gh = FakeGitHubClient(issues=issues)
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        collection = impl.collect_pool()

        pinned = [item for item in collection.items if item.ref == 999]
        assert [item.labels for item in pinned] == [("ready-for-agent", "priority")]

    def test_an_incomplete_fetch_is_not_authoritative(self) -> None:
        """A truncated read may not stand in for the whole backlog.

        ADR-0020 §2.13 already forbade concluding *emptiness* from a partial
        read; oldest-first extends the same rule to the *head* of the order,
        because the rows a ceiling cut off are the ones a Priority issue lands
        among. The candidates read are still returned — what they may not do is
        claim to be all of them.
        """
        gh = FakeGitHubClient(issues=[_make_issue(31)], issue_list_complete=False)
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        collection = impl.collect_pool()

        assert [item.ref for item in collection.items] == [31]
        assert collection.complete is False

    def test_reading_past_one_page_does_not_change_eligibility(self) -> None:
        """Completeness is a fetch property; the discriminator is untouched."""
        issues = [_make_issue(n) for n in range(1, 120)]
        issues.append(_make_issue(500, body="just a thought"))
        gh = FakeGitHubClient(issues=issues)
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        collection = impl.collect_pool()

        assert 500 not in [item.ref for item in collection.items]
        assert [e.ref for e in collection.exclusions] == [500]

    def test_a_pool_item_carries_the_timestamp_the_order_is_built_on(self) -> None:
        """Selection happens over collected items, so the field travels with them."""
        gh = FakeGitHubClient(
            issues=[_make_issue(31, created_at="2026-01-02T03:04:05Z")]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        [item] = impl.collect_pool().items

        assert item.created_at == "2026-01-02T03:04:05Z"


class TestPickup:
    """The authoritative read taken immediately before a Lane reservation."""

    def _source(self, **kw) -> GitHubIssueSource:
        return GitHubIssueSource(_silent_logger(), gh=FakeGitHubClient(**kw))

    def test_validates_and_enriches_an_eligible_candidate(self) -> None:
        gh = FakeGitHubClient(
            issues=[
                _make_issue(
                    31,
                    labels=["ready-for-agent", "parallel-safe"],
                    comments=(
                        gh_module.Comment(
                            author="octo", body="a note", created_at="2026-07-25"
                        ),
                    ),
                )
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        pickup = impl.pickup(31)

        assert pickup.validated is True
        assert pickup.item is not None
        assert pickup.item.ref == 31
        assert pickup.item.labels == ("ready-for-agent", "parallel-safe")
        # The pickup read is what supplies comments + rendered prompt content.
        assert "a note" in pickup.item.rendered_block

    def test_closed_issue_is_stale(self) -> None:
        impl = self._source(
            issues=[
                _make_issue(
                    31, state="CLOSED", labels=["ready-for-agent", "parallel-safe"]
                )
            ]
        )
        pickup = impl.pickup(31)
        assert pickup.outcome == sources_module.PICKUP_STALE
        assert pickup.item is None

    def test_issue_that_lost_parallel_safe_is_stale(self) -> None:
        """Parallel-safe is a human assertion; losing it makes the pickup stale."""
        impl = self._source(issues=[_make_issue(31, labels=["ready-for-agent"])])
        assert impl.pickup(31).outcome == sources_module.PICKUP_STALE

    def test_issue_that_lost_ready_for_agent_is_stale(self) -> None:
        impl = self._source(issues=[_make_issue(31, labels=["parallel-safe"])])
        assert impl.pickup(31).outcome == sources_module.PICKUP_STALE

    def test_issue_whose_body_lost_the_afk_shape_is_stale(self) -> None:
        impl = self._source(
            issues=[
                _make_issue(
                    31,
                    body="someone rewrote this",
                    labels=["ready-for-agent", "parallel-safe"],
                )
            ]
        )
        assert impl.pickup(31).outcome == sources_module.PICKUP_STALE

    def test_read_failure_is_unavailable_not_stale(self) -> None:
        """A transient read failure must not permanently drop recoverable work."""
        impl = self._source(
            issues=[_make_issue(31, labels=["ready-for-agent", "parallel-safe"])],
            issue_view_errors={31: gh_module.GhError(["gh"], 1, "HTTP 502")},
        )
        pickup = impl.pickup(31)
        assert pickup.outcome == sources_module.PICKUP_UNAVAILABLE
        assert pickup.item is None

    def test_priority_does_not_excuse_a_missing_parallel_safe(self) -> None:
        """**Priority** reorders; it does not admit (#395, contract §3.2).

        Guarding the *non*-interaction, now that ``git-loopy init`` provisions
        ``priority`` and an operator can actually apply it. The hazard is
        specific: **Priority** wins every ordering comparison, so an issue
        carrying it arrives at pickup first — and a Lane admitting it without
        the human's concurrency assertion is exactly the unsafe concurrent work
        ``parallel-safe`` exists to prevent, reached by the label most likely to
        be applied in a hurry.
        """
        impl = self._source(
            issues=[_make_issue(31, labels=["ready-for-agent", "priority"])]
        )
        assert impl.pickup(31).outcome == sources_module.PICKUP_STALE

    def test_priority_does_not_excuse_a_missing_ready_for_agent(self) -> None:
        """Priority on an untriaged issue is still an untriaged issue."""
        impl = self._source(
            issues=[_make_issue(31, labels=["parallel-safe", "priority"])]
        )
        assert impl.pickup(31).outcome == sources_module.PICKUP_STALE

    def test_priority_does_not_excuse_a_body_that_is_not_afk_ready(self) -> None:
        """The discriminator keeps its exact meaning; urgency is not a substitute."""
        impl = self._source(
            issues=[
                _make_issue(
                    31,
                    body="URGENT: fix the thing",
                    labels=["ready-for-agent", "parallel-safe", "priority"],
                )
            ]
        )
        assert impl.pickup(31).outcome == sources_module.PICKUP_STALE


class TestRollingSourceSplit:
    """Rolling dispatch is a GitHub-only capability, asked for structurally."""

    def test_github_source_satisfies_the_rolling_protocol(self) -> None:
        impl = GitHubIssueSource(_silent_logger(), gh=FakeGitHubClient())
        assert isinstance(impl, sources_module.RollingIssueSource)

    def test_prds_source_does_not_satisfy_the_rolling_protocol(self, tmp_path: Path) -> None:
        """Local markdown carries no Parallel-safe assertion, so it never Lanes.

        Rolling dispatch must be able to *ask* rather than assume; a PRDs source
        that structurally answered would have to fake an eligibility signal it
        cannot have.
        """
        impl = PrdsIssueSource(tmp_path, _silent_logger())
        assert isinstance(impl, IssueSource)
        assert not isinstance(impl, sources_module.RollingIssueSource)

    def test_serial_collection_is_unchanged_by_the_rolling_seam(self) -> None:
        """The serial Iteration still gets its full enriched, rendered Pool."""
        gh = FakeGitHubClient(
            issues=[
                _make_issue(
                    31,
                    labels=["ready-for-agent"],
                    comments=(
                        gh_module.Comment(
                            author="octo", body="serial note", created_at="2026-07-25"
                        ),
                    ),
                ),
                _make_issue(7, labels=["ready-for-agent", "parallel-safe"]),
            ]
        )
        impl = GitHubIssueSource(_silent_logger(), gh=gh)

        pool = list(impl.collect_pool().items)

        # Both issues, enriched — the rolling seam never narrows serial collection
        # to Parallel-safe work or drops the comment enrichment. The sequence is
        # §3.2's (#394), which is a property of the read and not of the seam
        # under test: neither issue is dated, so the issue number decides.
        assert [i.ref for i in pool] == [7, 31]
        assert "serial note" in pool[1].rendered_block
        assert gh.issue_view_calls == [7, 31]


# --------------------------------------------------------------------------- #
# Rate-limit pressure reporting (#309, #219 §6)                                #
# --------------------------------------------------------------------------- #


class TestRateLimitReporting:
    """The source relays the 429 **Pressure signal** its client observed."""

    def test_the_source_reports_the_throttling_its_client_saw(self) -> None:
        """#219 §6: the adaptive controller reads 429s through the source.

        The Rolling-dispatch driver holds an :class:`IssueSource`, never a
        ``gh`` client, so the count has to surface here for the **Effective
        Lane limit** to react to it at all.
        """
        throttled = gh_module.GhError(
            ["gh", "issue", "view", "42"],
            1,
            "HTTP 403: API rate limit exceeded for user ID 1",
        )
        client = FakeGitHubClient(issue_view_errors={42: throttled})
        source = GitHubIssueSource(_silent_logger(), gh=client)
        assert source.rate_limited_reads() == 0

        for _ in range(2):
            with pytest.raises(gh_module.GhError):
                client.issue_view(42)

        assert source.rate_limited_reads() == 2

    def test_an_ordinary_failed_read_is_not_reported_as_throttling(self) -> None:
        """A missing issue is not back-pressure and must not cost a **Lane**."""
        client = FakeGitHubClient()
        source = GitHubIssueSource(_silent_logger(), gh=client)

        with pytest.raises(gh_module.GhError):
            client.issue_view(999)

        assert source.rate_limited_reads() == 0

    def test_a_client_that_cannot_count_leaves_the_signal_unknown(self) -> None:
        """#219 §11: an unobservable signal is unknown, never an observed zero.

        A ``gh`` seam that does not meter throttling has seen no evidence of
        calm — reporting ``0`` would let a blind Run climb its **Lane** count
        on the absence of bad news.
        """

        class _UnmeteredClient:
            """A ``gh`` seam predating the 429 **Pressure signal**."""

        source = GitHubIssueSource(
            _silent_logger(),
            gh=_UnmeteredClient(),  # type: ignore[arg-type]
        )

        assert source.rate_limited_reads() is None


# --------------------------------------------------------------------------- #
# The invocation-scoped pin (#396)                                            #
# --------------------------------------------------------------------------- #


class TestThePinReachesSelection:
    """`--issue N` promotes N to the head of the Pool the runner selects from."""

    def test_a_pinned_issue_heads_the_pool_ahead_of_older_issues(self) -> None:
        gh = FakeGitHubClient(
            issues=[
                _make_issue(10, created_at="2024-01-01T00:00:00Z"),
                _make_issue(20, created_at="2025-01-01T00:00:00Z"),
                _make_issue(30, created_at="2026-01-01T00:00:00Z"),
            ]
        )
        source = GitHubIssueSource(_silent_logger(), gh=gh, pin=30)

        refs = [item.ref for item in source.collect_pool().items]

        assert refs == [30, 10, 20]

    def test_an_unpinned_pool_is_still_oldest_first(self) -> None:
        gh = FakeGitHubClient(
            issues=[
                _make_issue(10, created_at="2024-01-01T00:00:00Z"),
                _make_issue(30, created_at="2026-01-01T00:00:00Z"),
            ]
        )
        source = GitHubIssueSource(_silent_logger(), gh=gh)

        refs = [item.ref for item in source.collect_pool().items]

        assert refs == [10, 30]

    def test_the_pin_reaches_rolling_membership_too(self) -> None:
        """A **Lane** takes the head of the same order a serial Pickup does.

        ``RollingPool`` walks its cache front to back, so promoting in the
        membership read is the whole of what a pin has to do in Parallel mode —
        the scheduler needs no notion of one.
        """
        gh = FakeGitHubClient(
            issues=[
                _make_issue(
                    10,
                    created_at="2024-01-01T00:00:00Z",
                    labels=["ready-for-agent", "parallel-safe"],
                ),
                _make_issue(
                    30,
                    created_at="2026-01-01T00:00:00Z",
                    labels=["ready-for-agent", "parallel-safe"],
                ),
            ]
        )
        source = GitHubIssueSource(_silent_logger(), gh=gh, pin=30)

        refs = [c.ref for c in source.shallow_membership().candidates]

        assert refs == [30, 10]

    def test_the_pin_does_not_remove_any_other_issue_from_the_pool(self) -> None:
        """A pin bypasses order and *nothing else* (ADR-0032).

        Restricting the Pool to the pinned issue would be the same runner
        working a smaller backlog, and would silently end the Run the moment
        the pin closed. The remainder is still there, still eligible, and
        resumes §3.2 order behind the pin.
        """
        gh = FakeGitHubClient(
            issues=[
                _make_issue(10, created_at="2024-01-01T00:00:00Z"),
                _make_issue(20, created_at="2025-01-01T00:00:00Z"),
            ]
        )
        source = GitHubIssueSource(_silent_logger(), gh=gh, pin=20)

        assert {item.ref for item in source.collect_pool().items} == {10, 20}


class TestThePinIsValidatedAtPreflight:
    """An ineligible pin fails the invocation. It never falls back to order."""

    def _preflight(self, gh: FakeGitHubClient, **kwargs: Any) -> tuple[int | None, str]:
        logger = _silent_logger()
        source = GitHubIssueSource(logger, gh=gh, **kwargs)
        with _capture(logger) as records:
            rc = source.preflight()
        return rc, "\n".join(r.getMessage() for r in records)

    def test_an_eligible_pin_passes_preflight(self) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(7)])

        rc, _ = self._preflight(gh, pin=7)

        assert rc is None

    def test_an_unpinned_invocation_never_asks_the_tracker(self) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(7)])

        rc, _ = self._preflight(gh)

        assert rc is None
        assert gh.issue_view_calls == []

    def test_a_pin_lacking_ready_for_agent_fails_the_invocation(self) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(7, labels=[])])

        rc, logged = self._preflight(gh, pin=7)

        assert rc == 1
        assert "ready-for-agent" in logged

    def test_a_pin_failing_the_discriminator_names_the_missing_section(self) -> None:
        gh = FakeGitHubClient(
            issues=[_make_issue(7, body="## What to build\nthing, but no criteria")]
        )

        rc, logged = self._preflight(gh, pin=7)

        assert rc == 1
        assert "## Acceptance criteria" in logged

    def test_a_closed_pin_fails_the_invocation(self) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(7, state="CLOSED")])

        rc, logged = self._preflight(gh, pin=7)

        assert rc == 1
        assert "closed" in logged

    def test_a_missing_pin_fails_the_invocation(self) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(7)])

        rc, logged = self._preflight(gh, pin=404)

        assert rc == 1
        assert "#404" in logged

    def test_an_unreadable_pin_fails_the_invocation(self) -> None:
        gh = FakeGitHubClient(
            issues=[_make_issue(7)],
            issue_view_errors={
                7: gh_module.GhError("boom", returncode=1, stderr_tail="boom")
            },
        )

        rc, logged = self._preflight(gh, pin=7)

        assert rc == 1
        assert "#7" in logged

    def test_a_parallel_invocation_refuses_a_pin_that_is_not_parallel_safe(
        self,
    ) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(7, labels=["ready-for-agent"])])

        rc, logged = self._preflight(gh, pin=7, pin_requires_parallel_safe=True)

        assert rc == 1
        assert "parallel-safe" in logged

    def test_a_serial_invocation_does_not_require_parallel_safe(self) -> None:
        gh = FakeGitHubClient(issues=[_make_issue(7, labels=["ready-for-agent"])])

        rc, _ = self._preflight(gh, pin=7)

        assert rc is None

    def test_an_ineligible_pin_is_refused_before_any_pool_is_read(self) -> None:
        """The refusal must land at preflight, not as an empty-looking Pool.

        A pin that failed later would have already been indistinguishable from
        a backlog that simply did not contain it — which is the fall back to
        normal order this ticket exists to prevent.
        """
        gh = FakeGitHubClient(issues=[_make_issue(7, labels=[]), _make_issue(8)])

        rc, _ = self._preflight(gh, pin=7)

        assert rc == 1
        assert gh.issue_list_calls == []
