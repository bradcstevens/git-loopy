"""``git_loopy.telemetry`` — opt-in OpenTelemetry tracing.

This subpackage contains the no-op-by-default OTel seam that the rest of
the runner uses for span emission. The single public module is
:mod:`git_loopy.telemetry.otel`. See its docstring for the activation
contract and the call-site usage pattern.

The subpackage exists as a directory (rather than a top-level
``telemetry.py``) so issue #12's contract — that no ``opentelemetry-*``
package is imported when OTel is disabled — can be cleanly enforced by
the lazy imports inside :mod:`git_loopy.telemetry.otel`. Operators who
install with ``uv sync`` (no ``--extra otel``) see exactly zero
``opentelemetry`` modules in ``sys.modules`` after a ``git-loopy``
invocation that doesn't touch the wiring.
"""
