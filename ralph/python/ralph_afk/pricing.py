"""``ralph_afk.pricing`` — live model-price lookup and cost estimation.

The runner estimates cost from observed SDK token counts. GitHub Copilot
billing is not exposed by the SDK, so these figures remain **estimates**; the
default source is the live LiteLLM model pricing/context catalog, cached on
disk for fast/offline startup. Explicit ``RALPH_PRICING_FILE`` TOML overrides
still short-circuit the live lookup for private, pinned, or air-gapped use.

Public surface:

* :func:`load_pricing` — explicit TOML path / ``RALPH_PRICING_FILE`` override,
  otherwise live catalog fetch with a 24-hour cache and packaged fallback.
* :func:`estimate_cost` — token counts → USD :class:`~decimal.Decimal`; ``None``
  for unknown model so callers render ``—`` rather than zero.
* :func:`context_utilisation` — cumulative tokens → ``(used, window, fraction)``;
  ``None`` for unknown model.
* :class:`Pricing`, :class:`ModelPricing` — frozen value objects.
* :exc:`PricingError` — raised for explicit TOML override failures and used to
  carry live-fetch/parse failures into non-aborting fallback paths.

Design notes:

* **Stdlib only.** No dependency is added for a startup-time HTTP GET.
* **Decimal not float.** Live JSON numbers flow through ``str()`` before
  :class:`Decimal`, then convert per-token prices to per-million-token prices.
* **Unknown-model semantics.** Both :func:`estimate_cost` and
  :func:`context_utilisation` return ``None`` (not zero, not a fallback guess)
  so the renderer can show ``—`` for models absent from the live catalog.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from pathlib import Path
from typing import Callable, Mapping


class PricingError(ValueError):
    """Raised when a pricing source cannot be fetched or parsed."""


@dataclass(frozen=True)
class ModelPricing:
    """Per-model pricing entry. Prices in USD per million tokens."""

    input_per_mtok: Decimal
    output_per_mtok: Decimal
    context_window: int


@dataclass(frozen=True)
class Pricing:
    """Resolved pricing table keyed by source catalog model name."""

    models: Mapping[str, ModelPricing]
    source: str = "unknown"
    source_error: str | None = None

    def get(self, model: str) -> ModelPricing | None:
        """Return the entry for ``model`` or ``None`` if absent.

        Copilot CLI model ids can include reasoning-effort suffixes and can use
        shorter aliases than upstream provider catalogs. Resolution therefore
        tries the exact id first, then a small set of deterministic aliases.
        """
        for candidate in _candidate_model_keys(model):
            found = self.models.get(candidate)
            if found is not None:
                return found
        return None


PricingFetcher = Callable[[str, float], bytes]

DEFAULT_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

_REQUIRED_TOML_FIELDS: tuple[str, ...] = (
    "input_per_mtok",
    "output_per_mtok",
    "context_window",
)
_ENV_OVERRIDE = "RALPH_PRICING_FILE"
_MTOK: Decimal = Decimal(1_000_000)
_CACHE_TTL_SECONDS: float = 24 * 60 * 60
_FETCH_TIMEOUT_SECONDS: float = 10.0
_MAX_CATALOG_BYTES: int = 8 * 1024 * 1024
_REASONING_SUFFIXES: tuple[str, ...] = (
    "minimal",
    "xhigh",
    "high",
    "medium",
    "low",
    "none",
    "max",
)
_KNOWN_MODEL_ALIASES: Mapping[str, tuple[str, ...]] = {
    "claude-opus-4.7-xhigh": (
        "anthropic.claude-opus-4-7",
        "openrouter/anthropic/claude-opus-4.7",
    ),
    "claude-opus-4.7": (
        "anthropic.claude-opus-4-7",
        "openrouter/anthropic/claude-opus-4.7",
    ),
    "claude-sonnet-4.6": (
        "anthropic.claude-sonnet-4-6",
        "openrouter/anthropic/claude-sonnet-4.6",
    ),
}


def _packaged_path() -> Path:
    """Resolve the packaged fallback ``pricing.toml``."""
    return Path(str(files("ralph_afk") / "pricing.toml"))


def _default_cache_path() -> Path:
    """Return the process-local cache path for the live catalog snapshot."""
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return base / "ralph-afk" / "litellm-pricing.json"


def load_pricing(
    path: Path | None = None,
    *,
    url: str = DEFAULT_PRICING_URL,
    fetcher: PricingFetcher | None = None,
    cache_path: Path | None = None,
    cache_ttl_seconds: float = _CACHE_TTL_SECONDS,
    timeout_seconds: float = _FETCH_TIMEOUT_SECONDS,
) -> Pricing:
    """Load model pricing.

    Resolution order:

    1. The explicit TOML ``path`` argument, if provided.
    2. The ``RALPH_PRICING_FILE`` TOML override, if set and non-empty.
    3. A fresh cached live catalog snapshot.
    4. A live LiteLLM catalog fetch, written back to cache on success.
    5. A stale cache, then the packaged TOML fallback, with ``source_error`` set.

    Explicit TOML override failures raise :class:`PricingError`; live-source
    failures do not abort the AFK loop because pricing is advisory.
    """
    if path is None:
        env_override = os.environ.get(_ENV_OVERRIDE) or ""
        path = Path(env_override) if env_override.strip() else None
    if path is not None:
        return _load_toml_pricing(path, source=f"file:{path}")

    cache = cache_path if cache_path is not None else _default_cache_path()
    fresh_cache = _read_cache(cache, max_age_seconds=cache_ttl_seconds)
    if fresh_cache is not None:
        try:
            return _parse_live_catalog(fresh_cache, source=f"cache:{cache}")
        except PricingError:
            # Treat corrupt cache as a cache miss; a fresh live fetch below can
            # repair it, and the packaged fallback still protects offline runs.
            pass

    try:
        raw = _fetch_live_catalog(
            url,
            fetcher=fetcher,
            timeout_seconds=timeout_seconds,
        )
        pricing = _parse_live_catalog(raw, source=f"live:{url}")
        cache_error = _write_cache(cache, raw)
        if cache_error is not None:
            return Pricing(
                models=pricing.models,
                source=pricing.source,
                source_error=f"pricing cache write failed: {cache_error}",
            )
        return pricing
    except PricingError as exc:
        live_error = f"live pricing unavailable: {exc}"

    stale_cache = _read_cache(cache, max_age_seconds=None)
    if stale_cache is not None:
        try:
            stale = _parse_live_catalog(stale_cache, source=f"stale-cache:{cache}")
            return Pricing(
                models=stale.models,
                source=stale.source,
                source_error=live_error,
            )
        except PricingError as exc:
            live_error = f"{live_error}; cached pricing unusable: {exc}"

    try:
        fallback = _load_toml_pricing(
            _packaged_path(),
            source=f"packaged:{_packaged_path()}",
        )
        return Pricing(
            models=fallback.models,
            source=fallback.source,
            source_error=live_error,
        )
    except PricingError as exc:
        return Pricing(
            models={},
            source="unavailable",
            source_error=f"{live_error}; packaged fallback failed: {exc}",
        )


def _load_toml_pricing(path: Path, *, source: str) -> Pricing:
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise PricingError(f"Pricing file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PricingError(
            f"Pricing file {path} is not valid TOML: {exc}"
        ) from exc

    models_raw = raw.get("models")
    if not isinstance(models_raw, dict):
        raise PricingError(
            f"Pricing file {path} is missing required top-level [models] table"
        )

    models: dict[str, ModelPricing] = {}
    for model_name, entry in models_raw.items():
        if not isinstance(entry, dict):
            raise PricingError(
                f"Pricing file {path}: [models.{model_name!r}] must be a table"
            )
        for field in _REQUIRED_TOML_FIELDS:
            if field not in entry:
                raise PricingError(
                    f"Pricing file {path}: [models.{model_name!r}] "
                    f"is missing required field {field!r}"
                )
        try:
            models[model_name] = ModelPricing(
                input_per_mtok=Decimal(str(entry["input_per_mtok"])),
                output_per_mtok=Decimal(str(entry["output_per_mtok"])),
                context_window=int(entry["context_window"]),
            )
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise PricingError(
                f"Pricing file {path}: [models.{model_name!r}] "
                f"has invalid numeric value: {exc}"
            ) from exc

    return Pricing(models=models, source=source)


def _fetch_live_catalog(
    url: str,
    *,
    fetcher: PricingFetcher | None,
    timeout_seconds: float,
) -> bytes:
    if not url.startswith("https://"):
        raise PricingError("live pricing URL must use https")
    try:
        raw = (
            fetcher(url, timeout_seconds)
            if fetcher is not None
            else _default_fetcher(url, timeout_seconds)
        )
    except PricingError:
        raise
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise PricingError(str(exc)) from exc
    if len(raw) > _MAX_CATALOG_BYTES:
        raise PricingError(
            f"live pricing response too large ({len(raw)} bytes; "
            f"max {_MAX_CATALOG_BYTES})"
        )
    return raw


def _default_fetcher(url: str, timeout_seconds: float) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as resp:
            raw = resp.read(_MAX_CATALOG_BYTES + 1)
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise PricingError(str(exc)) from exc
    if len(raw) > _MAX_CATALOG_BYTES:
        raise PricingError(
            f"live pricing response too large ({len(raw)} bytes; "
            f"max {_MAX_CATALOG_BYTES})"
        )
    return raw


def _read_cache(path: Path, *, max_age_seconds: float | None) -> bytes | None:
    try:
        if max_age_seconds is not None:
            age = time.time() - path.stat().st_mtime
            if age > max_age_seconds:
                return None
        return path.read_bytes()
    except OSError:
        return None


def _write_cache(path: Path, raw: bytes) -> str | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return None
    except OSError as exc:
        return str(exc)


def _parse_live_catalog(raw: bytes, *, source: str) -> Pricing:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PricingError(f"live pricing catalog is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PricingError("live pricing catalog must be a JSON object")

    models: dict[str, ModelPricing] = {}
    for model_name, entry in parsed.items():
        if not isinstance(model_name, str) or not isinstance(entry, dict):
            continue
        if "input_cost_per_token" not in entry or "output_cost_per_token" not in entry:
            continue
        context_window = (
            entry.get("max_input_tokens")
            or entry.get("max_tokens")
            or entry.get("context_window")
        )
        if context_window is None:
            continue
        try:
            models[model_name] = ModelPricing(
                input_per_mtok=_per_token_to_mtok(entry["input_cost_per_token"]),
                output_per_mtok=_per_token_to_mtok(entry["output_cost_per_token"]),
                context_window=int(context_window),
            )
        except (InvalidOperation, ValueError, TypeError):
            continue

    return Pricing(models=models, source=source)


def _per_token_to_mtok(value: object) -> Decimal:
    return Decimal(str(value)) * _MTOK


def _candidate_model_keys(model: str) -> tuple[str, ...]:
    candidates: list[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    add(model)
    for alias in _KNOWN_MODEL_ALIASES.get(model, ()):
        add(alias)

    stripped = _strip_reasoning_suffix(model)
    add(stripped)
    for alias in _KNOWN_MODEL_ALIASES.get(stripped, ()):
        add(alias)

    if stripped.startswith("claude-"):
        dash_variant = stripped.replace(".", "-")
        add(f"anthropic.{dash_variant}")
        add(f"openrouter/anthropic/{stripped}")

    return tuple(candidates)


def _strip_reasoning_suffix(model: str) -> str:
    for suffix in _REASONING_SUFFIXES:
        marker = f"-{suffix}"
        if model.endswith(marker):
            return model[: -len(marker)]
    return model


def estimate_cost(
    model: str,
    tokens_in: int,
    tokens_out: int,
    pricing: Pricing,
) -> Decimal | None:
    """Estimate USD cost for a single iteration.

    Returns ``None`` (not zero) for unknown models so the renderer surfaces
    ``—`` rather than silently understating cost.
    """
    entry = pricing.get(model)
    if entry is None:
        return None
    in_cost = (Decimal(tokens_in) * entry.input_per_mtok) / _MTOK
    out_cost = (Decimal(tokens_out) * entry.output_per_mtok) / _MTOK
    return in_cost + out_cost


def context_utilisation(
    model: str,
    cumulative_tokens: int,
    pricing: Pricing,
) -> tuple[int, int, float] | None:
    """Return ``(used, window, fraction)`` or ``None`` for unknown model."""
    entry = pricing.get(model)
    if entry is None:
        return None
    used = cumulative_tokens
    window = entry.context_window
    fraction = (used / window) if window > 0 else 0.0
    return (used, window, fraction)
