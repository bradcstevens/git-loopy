"""Compatibility identities this distribution declares.

The Wrapper contract version lived in `continuation.py` until Continuation was
decommissioned (ADR-0046). It never belonged there — it is the family's identity,
not one feature's — so it lives here now, where a reader looking for "what contract
does this distribution implement" finds it without reading a feature module.
"""

from __future__ import annotations

#: The Wrapper contract revision this distribution implements. `docs/wrapper-contract.md`
#: carries the same number in its header; the two are changed together.
WRAPPER_CONTRACT_VERSION = "2.0"

__all__ = ["WRAPPER_CONTRACT_VERSION"]
