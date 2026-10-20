"""Process-owned idle-sleep prevention for local macOS Runs (ADR-0092)."""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable
from contextlib import ExitStack
from threading import Lock


class IdleSleepAssertion:
    """Own exactly one assertion; IOKit also releases it on process death.

    No child process, display assertion, user-activity declaration or power
    setting is involved. Closing a lid can still suspend the machine.
    """

    def __init__(self, *, enabled: bool, local: bool) -> None:
        self._enabled = enabled
        self._local = local
        self._release: Callable[[], None] | None = None
        self._diag: logging.Logger | None = None

    def start(self, diag: logging.Logger) -> None:
        if self._diag is not None:
            raise RuntimeError("idle-sleep assertion can only be started once")
        self._diag = diag
        if not self._local:
            state = "remote"
        elif sys.platform != "darwin":
            state = "unsupported"
        elif not self._enabled:
            state = "disabled"
        else:
            try:
                self._release = _acquire_assertion()
            except OSError as exc:
                diag.error("idle-sleep assertion: not held (%s)", exc)
                raise
            state = "held (display sleep and screen lock unchanged)"
        diag.info("idle-sleep assertion: %s", state)

    def close(self) -> None:
        if self._release is not None:
            try:
                self._release()
            except OSError as exc:
                if self._diag is not None:
                    self._diag.error("could not release idle-sleep assertion: %s", exc)
                raise
            self._release = None
            if self._diag is not None:
                self._diag.info("idle-sleep assertion: released")


def _acquire_assertion() -> Callable[[], None]:
    cf = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    io = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    cf.CFStringCreateWithCString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32,
    ]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None
    io.IOPMAssertionCreateWithName.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    io.IOPMAssertionCreateWithName.restype = ctypes.c_int32
    io.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
    io.IOPMAssertionRelease.restype = ctypes.c_int32
    with ExitStack() as strings:
        def string(value: bytes) -> int:
            ref = cf.CFStringCreateWithCString(None, value, 0x08000100)
            if not ref:
                raise OSError("could not allocate idle-sleep assertion name")
            strings.callback(cf.CFRelease, ref)
            return ref

        kind = string(b"PreventUserIdleSystemSleep")
        name = string(b"git-loopy Run")
        assertion_id = ctypes.c_uint32()
        result = io.IOPMAssertionCreateWithName(
            kind, 255, name, ctypes.byref(assertion_id),
        )
        if result != 0:
            raise OSError(f"IOPMAssertionCreateWithName failed: {result:#x}")

    def release() -> None:
        result = io.IOPMAssertionRelease(assertion_id)
        if result != 0:
            raise OSError(f"IOPMAssertionRelease failed: {result:#x}")

    return release


class SuspensionClock:
    """Measure sleep, not busy execution or adjustments to the civil clock."""

    def __init__(
        self, *, continuous: Callable[[], float], awake: Callable[[], float],
    ) -> None:
        self._continuous = continuous
        self._awake = awake
        self._lock = Lock()
        self._previous = continuous() - awake()

    def poll(self) -> float:
        with self._lock:
            current = self._continuous() - self._awake()
            elapsed = current - self._previous
            self._previous = current
        return max(0.0, elapsed)

    @classmethod
    def for_local_macos(cls, *, local: bool) -> SuspensionClock | None:
        if not local or sys.platform != "darwin":
            return None

        class Timebase(ctypes.Structure):
            _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]

        system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        system.mach_timebase_info.argtypes = [ctypes.POINTER(Timebase)]
        system.mach_timebase_info.restype = ctypes.c_int
        timebase = Timebase()
        result = system.mach_timebase_info(ctypes.byref(timebase))
        if result != 0 or timebase.denom == 0:
            raise OSError(f"mach_timebase_info failed: {result}")
        factor = timebase.numer / timebase.denom / 1_000_000_000
        system.mach_continuous_time.argtypes = []
        system.mach_continuous_time.restype = ctypes.c_uint64
        system.mach_absolute_time.argtypes = []
        system.mach_absolute_time.restype = ctypes.c_uint64
        return cls(
            continuous=lambda: system.mach_continuous_time() * factor,
            awake=lambda: system.mach_absolute_time() * factor,
        )
