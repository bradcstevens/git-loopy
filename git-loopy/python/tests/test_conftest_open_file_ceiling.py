"""Tests for the suite's file-descriptor ceiling raise in ``conftest.py``.

The suite's fixtures build many real git repositories and subprocesses across
thousands of tests. Hosts whose default soft ``RLIMIT_NOFILE`` is low (256 is
the common macOS shell default) start failing teardown with ``OSError: [Errno
24] Too many open files`` partway through the Python-suite Feedback loop
(AGENTS.md), for a reason that has nothing to do with what any test asserts —
raising the soft limit toward the hard limit at import time removes that
host-dependent ceiling. ``tests`` is a package (``tests/__init__.py``), so
``conftest`` imports as ``tests.conftest``.
"""

from __future__ import annotations

from tests import conftest


def test_a_soft_limit_below_a_bounded_hard_limit_is_raised_to_it() -> None:
    target = conftest._raised_open_file_ceiling(256, 4096, rlim_infinity=-1)
    assert target == 4096


def test_a_soft_limit_already_at_the_hard_limit_needs_no_raise() -> None:
    target = conftest._raised_open_file_ceiling(4096, 4096, rlim_infinity=-1)
    assert target is None


def test_a_soft_limit_above_the_hard_limit_needs_no_raise() -> None:
    # Not reachable through a well-formed rlimit pair, but the guard is a
    # plain comparison rather than an equality check, so pin that here too.
    target = conftest._raised_open_file_ceiling(8192, 4096, rlim_infinity=-1)
    assert target is None


def test_an_unbounded_hard_limit_is_capped_to_the_default_ceiling() -> None:
    target = conftest._raised_open_file_ceiling(
        256, 9223372036854775807, rlim_infinity=9223372036854775807
    )
    assert target == conftest._MINIMUM_OPEN_FILE_CEILING


def test_an_unbounded_hard_limit_needs_no_raise_once_already_past_the_ceiling() -> None:
    target = conftest._raised_open_file_ceiling(
        conftest._MINIMUM_OPEN_FILE_CEILING + 1,
        9223372036854775807,
        rlim_infinity=9223372036854775807,
    )
    assert target is None


class _FakeResourceModule:
    """A stand-in for the ``resource`` module's ``RLIMIT_NOFILE`` surface."""

    RLIMIT_NOFILE = "RLIMIT_NOFILE"

    def __init__(
        self, soft: int, hard: int, *, rlim_infinity: int = -1, refuses: bool = False
    ) -> None:
        self.RLIM_INFINITY = rlim_infinity
        self._soft = soft
        self._hard = hard
        self._refuses = refuses
        self.set_to: tuple[int, int] | None = None

    def getrlimit(self, which: str) -> tuple[int, int]:
        assert which == self.RLIMIT_NOFILE
        return (self._soft, self._hard)

    def setrlimit(self, which: str, limits: tuple[int, int]) -> None:
        assert which == self.RLIMIT_NOFILE
        if self._refuses:
            raise OSError("host refused the raise")
        self.set_to = limits


def test_the_wrapper_raises_the_soft_limit_through_the_resource_module() -> None:
    fake = _FakeResourceModule(256, 4096)
    conftest._raise_open_file_limit(resource_module=fake)
    assert fake.set_to == (4096, 4096)


def test_the_wrapper_is_a_no_op_when_no_raise_is_needed() -> None:
    fake = _FakeResourceModule(4096, 4096)
    conftest._raise_open_file_limit(resource_module=fake)
    assert fake.set_to is None


def test_a_host_that_refuses_the_raise_never_reaches_the_caller() -> None:
    fake = _FakeResourceModule(256, 4096, refuses=True)
    # Must not raise: a refused ceiling raise leaves the process as it was
    # rather than failing every test that imports conftest.
    conftest._raise_open_file_limit(resource_module=fake)
    assert fake.set_to is None


def test_a_missing_resource_module_is_a_no_op() -> None:
    conftest._raise_open_file_limit(resource_module=None)
