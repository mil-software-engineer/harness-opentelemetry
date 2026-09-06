"""Pure, side-effect-free pricing logic for the DSH token transformer.

Everything here is a plain function over simple values so it is trivially unit
testable. File loading (``load_pricing``) only reads/validates the JSON table
and never touches Prometheus or the request path; the wiring into counters lives
in ``app.py``.

Domain rules kept here (single source of truth):
- A telemetry *route* (e.g. ``deepseek-chat``) is normalised to a pricing-model
  name (e.g. ``deepseek-v4-flash``) via ``route_map``; an unmapped route is
  unknown and therefore not billed.
- Cost slots map one-to-one onto the token fields DSH records:
  ``uncachedInputTokens`` -> ``input_miss``, ``cacheReadTokens`` ->
  ``input_hit``, ``cacheWriteTokens`` -> ``input_write``, ``outputTokens`` ->
  ``output``. ``decodeTokens`` is deliberately not billed (its relationship to
  ``outputTokens``/reasoning is unconfirmed).
- Peak/off-peak is decided from the *record timestamp* (UTC), not request time.
- A price row is only active once its ``effective_from <= ts``; among active
  matching rows the most recent is chosen. No row -> not billed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

PEAK = "peak"
OFF_PEAK = "off_peak"

#: Default peak windows (inclusive UTC hours) and weekdays (ISO: Mon=1..Sun=7).
#: Kept here so callers can rely on sane defaults when none are configured.
DEFAULT_PEAK_WINDOWS: Sequence[Tuple[int, int]] = ((1, 4), (6, 10))
DEFAULT_PEAK_WEEKDAYS: Sequence[int] = (1, 2, 3, 4, 5)

#: Valid billing slots a price row may describe.
VALID_SLOTS = frozenset({"input_miss", "input_hit", "input_write", "output"})
VALID_PERIODS = frozenset({PEAK, OFF_PEAK})

#: Leaf names that override the configured ``default_effort`` for a record.
_EFFORT_LEAVES = frozenset({"effort", "reasoningEffort"})


class PricingError(ValueError):
    """Raised when the pricing table is missing or malformed."""


def normalize_model(route: str, route_map: Mapping[str, str]) -> Optional[str]:
    """Map a telemetry route to a pricing-model name.

    Returns the mapped model when ``route`` is present in ``route_map``,
    otherwise ``None`` (unknown route -> caller marks it unknown, not billed).
    """
    if not route:
        return None
    return route_map.get(route)


def classify_unknown(route: str) -> str:
    """Return a stable label describing an unmatched route.

    Kept separate from ``normalize_model`` so a future alert can distinguish
    "attribute missing" from "genuinely unknown value"; both currently resolve
    to a plain human-readable string for the (label-less) unknown counter.
    """
    return route if route else "unknown"


def effort_for(record_leaves: Mapping[str, Any], default_effort: str) -> str:
    """Resolve the ``effort`` tag for one record.

    Defaults to ``default_effort``; overridden only when a leaf whose final path
    component is ``effort``/``reasoningEffort`` carries a scalar value.
    """
    for key, val in record_leaves.items():
        if key.rsplit(".", 1)[-1] in _EFFORT_LEAVES:
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return str(val).lower()
    return default_effort


def period_for(
    timestamp_utc: datetime,
    peak_windows: Sequence[Sequence[int]] = DEFAULT_PEAK_WINDOWS,
    peak_weekdays: Sequence[int] = DEFAULT_PEAK_WEEKDAYS,
) -> str:
    """Classify a UTC timestamp as ``peak`` or ``off_peak``.

    Hours in ``peak_windows`` are inclusive bounds (e.g. ``[1, 4]`` covers hours
    1..4). A timestamp is peak only when its hour falls inside a window *and*
    its ISO weekday (Mon=1..Sun=7) is in ``peak_weekdays``.
    """
    if peak_weekdays and timestamp_utc.isoweekday() not in peak_weekdays:
        return OFF_PEAK
    hour = timestamp_utc.hour
    for start, end in peak_windows:
        if start <= hour <= end:
            return PEAK
    return OFF_PEAK


def _to_datetime(value: Any) -> Optional[datetime]:
    """Coerce an ``effective_from`` / timestamp into an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        # Interpret bare numbers as epoch *seconds* for compact hand-authored rows.
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        try:
            # Nanosecond epoch ints occasionally arrive as decimal strings.
            if text.isdigit() and len(text) >= 15:
                return datetime.fromtimestamp(int(text) / 1e9, tz=timezone.utc)
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def price_for(
    model: str,
    slot: str,
    period: str,
    ts: datetime,
    rows: Sequence[Mapping[str, Any]],
) -> Optional[Tuple[float, str]]:
    """Return ``(usd_per_1m, price_version)`` for the most recent active row.

    Among rows matching ``(model, slot, period)`` those with an
    ``effective_from <= ts`` are candidates; the one with the latest
    ``effective_from`` wins. Returns ``None`` when no row is active (not billed).
    """
    ts_utc = _to_datetime(ts)
    if ts_utc is None:
        return None
    best_row: Optional[Mapping[str, Any]] = None
    best_from: Optional[datetime] = None
    for row in rows:
        if row.get("model") != model or row.get("slot") != slot or row.get("period") != period:
            continue
        effective = _to_datetime(row.get("effective_from"))
        if effective is None or effective > ts_utc:
            continue
        if best_from is None or effective > best_from:
            best_from = effective
            best_row = row
    if best_row is None:
        return None
    return (
        float(best_row["price_per_million"]),
        str(best_row.get("pricing_version") or ""),
    )


def _validate_rows(rows: Any, pricing_version: str) -> List[Dict[str, Any]]:
    """Validate raw rows and denormalise ``pricing_version`` into each one."""
    if not isinstance(rows, list):
        raise PricingError("pricing config 'rows' must be a list")
    cleaned: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PricingError(f"pricing row #{index} must be an object")
        for field in ("model", "slot", "period", "effective_from"):
            if not row.get(field):
                raise PricingError(f"pricing row #{index} missing non-empty '{field}'")
        price = row.get("price_per_million")
        if not isinstance(price, (int, float)) or isinstance(price, bool) or price < 0:
            raise PricingError(f"pricing row #{index} has invalid 'price_per_million'")
        if row["slot"] not in VALID_SLOTS:
            raise PricingError(
                f"pricing row #{index} slot {row['slot']!r} not in {sorted(VALID_SLOTS)}"
            )
        if row["period"] not in VALID_PERIODS:
            raise PricingError(
                f"pricing row #{index} period {row['period']!r} not in {sorted(VALID_PERIODS)}"
            )
        if _to_datetime(row["effective_from"]) is None:
            raise PricingError(f"pricing row #{index} has unparsable 'effective_from'")
        cleaned_row = dict(row)
        cleaned_row["pricing_version"] = row.get("pricing_version", pricing_version)
        cleaned_row["price_per_million"] = float(price)
        cleaned_row["currency"] = row.get("currency", "USD")
        cleaned.append(cleaned_row)
    return cleaned


def load_pricing(path: str) -> Dict[str, Any]:
    """Load and validate a pricing JSON table from ``path``.

    Returns a dict of shape ``{pricing_version, default_effort, peak_windows_utc,
    peak_weekdays, route_map, rows}`` where every row additionally carries
    ``pricing_version`` (denormalised from the top level) and a default
    ``currency``. Raises :class:`PricingError` on any schema violation.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PricingError(f"cannot read pricing file {path!r}: {exc}") from exc

    if not isinstance(data, dict):
        raise PricingError("pricing config must be a JSON object")
    version = str(data.get("pricing_version") or "")
    if not version:
        raise PricingError("pricing config missing 'pricing_version'")
    if "default_effort" not in data or not isinstance(data.get("default_effort"), str):
        raise PricingError("pricing config missing string 'default_effort'")
    route_map = data.get("route_map")
    if not isinstance(route_map, dict):
        raise PricingError("pricing config 'route_map' must be an object")

    windows = data.get("peak_windows_utc") or DEFAULT_PEAK_WINDOWS
    weekdays = data.get("peak_weekdays") or DEFAULT_PEAK_WEEKDAYS

    return {
        "pricing_version": version,
        "default_effort": data["default_effort"],
        "peak_windows_utc": [tuple(w) for w in windows],
        "peak_weekdays": list(weekdays),
        "route_map": {str(k): str(v) for k, v in route_map.items()},
        "rows": _validate_rows(data.get("rows"), version),
    }
