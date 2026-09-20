"""Unit tests for ``git_loopy.wrapper``.

These tests cover the deep, pure logic of the wrapper, including the
close-keyword regex (:func:`extract_close_refs`) against the full
keyword / case / separator / dedup / negative corpus.

Acceptance criteria reference: issue #3.
"""

from __future__ import annotations

import re

import pytest

from git_loopy import wrapper
from git_loopy.wrapper import (
    CHECKPOINT_TRAILER_KEY,
    CLOSE_KEYWORD_RE,
    AbandonmentGuard,
    StrikeLedger,
    checkpoint_message,
    did_iteration_make_progress,
    extract_close_refs,
    filter_to_pool,
    is_checkpoint_message,
)


# --------------------------------------------------------------------------- #
# Exit reasons                                                                  #
# --------------------------------------------------------------------------- #


def test_operator_stop_has_a_decided_nonzero_exit_code() -> None:
    assert wrapper.exit_code_for("operator_stop") == 1


# --------------------------------------------------------------------------- #
# CLOSE_KEYWORD_RE shape                                                       #
# --------------------------------------------------------------------------- #


def test_close_keyword_regex_is_pattern_specified_by_wrapper_contract() -> None:
    """The compiled regex is byte-for-byte the Wrapper-contract string.

    Drift here implies the shared close-keyword convention has been
    broken; the regex cannot be reformulated.
    """
    assert CLOSE_KEYWORD_RE.pattern == (
        r"(?i)(close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#(\d+)"
    )
    assert CLOSE_KEYWORD_RE.flags & re.IGNORECASE


# --------------------------------------------------------------------------- #
# extract_close_refs                                                           #
# --------------------------------------------------------------------------- #


def test_extract_close_refs_empty_string_returns_empty_list() -> None:
    assert extract_close_refs("") == []


def test_extract_close_refs_no_keywords_returns_empty_list() -> None:
    assert extract_close_refs("Just a normal commit body with #42 inside.") == []


def test_extract_close_refs_matches_single_keyword() -> None:
    assert extract_close_refs("Closes #42") == [42]


def test_extract_close_refs_matches_every_keyword_form() -> None:
    corpus = (
        "close #1 closes #2 closed #3 "
        "fix #4 fixes #5 fixed #6 "
        "resolve #7 resolves #8 resolved #9"
    )
    assert extract_close_refs(corpus) == [1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_extract_close_refs_is_case_insensitive() -> None:
    assert extract_close_refs("CLOSES #1 cLoSeS #2 Fixes #3 RESOLVED #4") == [
        1,
        2,
        3,
        4,
    ]


def test_extract_close_refs_dedupes_in_first_encounter_order() -> None:
    """Dedup preserves the first-encounter order — the PRD's specified shape.

    The POSIX grep oracle produces sorted output; the Python function
    preserves order so callers (notably ``loop.py``)
    can apply pool-filtering using the chronological order in which the
    agent's commits referenced each issue.
    """
    assert extract_close_refs("Closes #9 fixes #1 closes #9 resolves #1") == [9, 1]


def test_extract_close_refs_handles_multi_commit_input_via_boundary() -> None:
    corpus = (
        "Closes #20\n"
        "---COMMIT-BOUNDARY---\n"
        "Fixes #21\n"
        "---COMMIT-BOUNDARY---\n"
        "Resolves #22"
    )
    assert extract_close_refs(corpus) == [20, 21, 22]


def test_extract_close_refs_does_not_match_cross_repo_qualified_ref() -> None:
    """``Closes org/other-repo#42`` must NOT match.

    This is the load-bearing safety property: cross-repo references travel
    in commit bodies legitimately (e.g. referencing an issue in a related
    repo) and the wrapper must never accidentally close a same-numbered
    issue in the current repo. The regex enforces this by requiring
    whitespace immediately before ``#``.
    """
    assert extract_close_refs("Closes org/other-repo#42") == []


def test_extract_close_refs_does_not_match_keyword_glued_to_hash() -> None:
    assert extract_close_refs("Closes#42") == []


def test_extract_close_refs_does_not_match_keyword_inside_compound_word() -> None:
    """``Close-related #42`` must NOT match — the keyword must be followed
    by whitespace, not a hyphen.
    """
    assert extract_close_refs("Close-related #42") == []


def test_extract_close_refs_does_not_match_across_newlines() -> None:
    """``grep`` reads line-by-line, so a keyword on one line and ``#N`` on
    the next must not match.

    Python's ``\\s+`` would otherwise span the newline; ``extract_close_refs``
    therefore processes input line-by-line to preserve the POSIX grep oracle.
    """
    assert extract_close_refs("Closes\n#42") == []


def test_extract_close_refs_matches_tab_separator() -> None:
    assert extract_close_refs("Closes\t#42") == [42]


def test_extract_close_refs_matches_multiple_spaces_separator() -> None:
    assert extract_close_refs("Closes   #42") == [42]


def test_extract_close_refs_matches_multiple_refs_on_one_line() -> None:
    assert extract_close_refs("Closes #100 and fixes #200") == [100, 200]


def test_extract_close_refs_handles_markdown_header_prefix() -> None:
    """A keyword sitting after markdown header markup like ``## Closes #42``
    still matches — the keyword is the load-bearing token, not its
    surrounding punctuation.
    """
    assert extract_close_refs("## Closes #42") == [42]


# --------------------------------------------------------------------------- #
# filter_to_pool                                                               #
# --------------------------------------------------------------------------- #


def test_filter_to_pool_keeps_only_pool_members() -> None:
    assert filter_to_pool([1, 2, 3, 4], {2, 4}) == [2, 4]


def test_filter_to_pool_preserves_input_order() -> None:
    assert filter_to_pool([9, 1, 5], {1, 5, 9}) == [9, 1, 5]


def test_filter_to_pool_empty_pool_returns_empty_list() -> None:
    assert filter_to_pool([1, 2, 3], set()) == []


def test_filter_to_pool_empty_refs_returns_empty_list() -> None:
    assert filter_to_pool([], {1, 2}) == []


def test_filter_to_pool_dedup_is_caller_responsibility() -> None:
    """``filter_to_pool`` does not dedup — that's ``extract_close_refs``'s job.

    Documents the separation of concerns explicitly so a future refactor
    doesn't accidentally double-dedup or assume dedup happens here.
    """
    assert filter_to_pool([1, 1, 2], {1, 2}) == [1, 1, 2]


# --------------------------------------------------------------------------- #
# did_iteration_make_progress                                                  #
# --------------------------------------------------------------------------- #


def test_progress_zero_zero_is_no_progress() -> None:
    assert did_iteration_make_progress(0, 0) is False


def test_progress_nonzero_commits_is_progress() -> None:
    assert did_iteration_make_progress(1, 0) is True


def test_progress_nonzero_auto_closures_is_progress() -> None:
    assert did_iteration_make_progress(0, 1) is True


def test_progress_both_nonzero_is_progress() -> None:
    assert did_iteration_make_progress(3, 2) is True


# --------------------------------------------------------------------------- #
# Strike accounting and the separate Abandonment guard                         #
# --------------------------------------------------------------------------- #


def test_strikes_are_per_issue_and_have_no_ceiling() -> None:
    ledger = StrikeLedger()
    for ref in range(100):
        ledger.record_abandonment(ref)
        ledger.record_abandonment(ref)
    assert ledger.strikes == 100


def test_abandonment_guard_defaults_to_three_consecutive_abandonments() -> None:
    guard = AbandonmentGuard()
    assert guard.limit == 3
    for _ in range(2):
        guard.record_abandonment()
        assert not guard.reached
    guard.record_abandonment()
    assert guard.reached
    assert guard.consecutive == 3


def test_progress_resets_the_guard_but_refunds_no_strike() -> None:
    guard = AbandonmentGuard(limit=2)
    ledger = StrikeLedger()
    for ref in range(100):
        ledger.record_abandonment(ref)
        guard.record_abandonment()
        assert not guard.reached
        guard.record_progress()
        assert guard.consecutive == 0
    assert ledger.strikes == 100


def test_progress_during_a_drain_resets_the_guard() -> None:
    guard = AbandonmentGuard(limit=1)
    guard.record_abandonment()
    assert guard.reached
    guard.record_progress()
    assert not guard.reached
    guard.record_abandonment()
    assert guard.reached


@pytest.mark.parametrize("bad_limit", [0, -1, -100, True, 1.5])
def test_abandonment_guard_rejects_invalid_limits(bad_limit: int) -> None:
    with pytest.raises(ValueError, match="abandonment guard limit"):
        AbandonmentGuard(limit=bad_limit)


# --------------------------------------------------------------------------- #
# Import cleanliness — wrapper.py imports nothing outside stdlib.             #
# --------------------------------------------------------------------------- #


def test_wrapper_module_imports_only_stdlib() -> None:
    """``wrapper.py`` must remain a deep, pure module that imports nothing
    outside a tight stdlib allowlist.

    Allowlist semantics (not blacklist): any import outside the allowlist
    fails the test. This catches the easy-to-miss regressions: a stray
    ``from git_loopy import gh``, a relative import like ``from . import
    events``, or a third-party convenience like ``import httpx``. The
    close-keyword regex convention is unit-testable in isolation precisely
    because nothing here imports from anywhere else in the package.
    """
    import ast
    from pathlib import Path

    allowed_imports = {
        "__future__",
        "re",
        "dataclasses",
        "typing",
    }

    source = Path(wrapper.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert top in allowed_imports, (
                    f"wrapper.py must not import {alias.name!r} "
                    f"(allowlist: {sorted(allowed_imports)})"
                )
        elif isinstance(node, ast.ImportFrom):
            # Reject relative imports outright — wrapper.py has no peers
            # it should ever pull from.
            assert node.level == 0, (
                f"wrapper.py must not use relative imports "
                f"(found level={node.level})"
            )
            module = node.module or ""
            top = module.split(".")[0]
            assert top in allowed_imports, (
                f"wrapper.py must not import from {module!r} "
                f"(allowlist: {sorted(allowed_imports)})"
            )


# --------------------------------------------------------------------------- #
# Checkpoint message contract (issue #32 — runner Checkpoint, ADR-0004)        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ref", [32, None, "prds/featA/001-ready.md"])
def test_checkpoint_message_is_close_keyword_free(ref) -> None:
    """A Checkpoint must never trip the auto-close backstop / GitHub on push."""
    assert extract_close_refs(checkpoint_message(ref)) == []


def test_checkpoint_message_carries_trailer_with_int_ref() -> None:
    msg = checkpoint_message(32)
    assert f"{CHECKPOINT_TRAILER_KEY}: 32" in msg
    assert is_checkpoint_message(msg) is True


def test_checkpoint_message_references_active_issue_in_subject() -> None:
    subject = checkpoint_message(32).split("\n", 1)[0]
    assert "32" in subject


def test_checkpoint_message_unattributed_when_ref_is_none() -> None:
    msg = checkpoint_message(None)
    assert f"{CHECKPOINT_TRAILER_KEY}: unattributed" in msg
    assert is_checkpoint_message(msg) is True


def test_checkpoint_message_carries_trailer_with_str_ref() -> None:
    msg = checkpoint_message("prds/featA/001-ready.md")
    assert f"{CHECKPOINT_TRAILER_KEY}: prds/featA/001-ready.md" in msg


def test_checkpoint_message_does_not_embed_hash_ref() -> None:
    """No ``#N`` anywhere, so a Checkpoint never creates a GitHub cross-ref."""
    assert "#" not in checkpoint_message(32)


def test_is_checkpoint_message_false_for_agent_commit() -> None:
    assert is_checkpoint_message("feat(x): real work\n\nCloses #5") is False


def test_is_checkpoint_message_tolerates_surrounding_whitespace() -> None:
    assert is_checkpoint_message(f"sub\n\n   {CHECKPOINT_TRAILER_KEY}: 7  ") is True
