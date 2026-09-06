"""DSH token-telemetry transformer.

Receives OTLP/HTTP **logs** (JSON encoding) pushed by the OTel Collector,
extracts the numeric usage fields that DSH records as log data, and converts
them into a ``dsh_tokens_total`` Prometheus counter labelled by token type,
session, model and tool.

Why this component exists
-------------------------
DSH emits OTLP *logs*, never metric series. The OTel Collector has no built-in
"log record -> Prometheus counter" transform, so a small log->metric generator
must live upstream of Prometheus. This FastAPI service is that generator: it is
the collector's ``otlphttp`` export target and Prometheus' scrape target, and it
owns the only business logic in the pipeline (see ``configs/otel.yaml``).

Keep this module intentionally simple and single-responsibility.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence, Tuple

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from starlette.responses import JSONResponse

logger = logging.getLogger("transformer")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# DSH records these usage fields (in log `body`/`attributes` as JSON). The keys
# are matched case-sensitively on the *leaf* name so nesting depth does not
# matter; each field maps onto the token-type label used in Prometheus.
TOKEN_FIELDS: Mapping[str, str] = {
    "uncachedInputTokens": "input",
    "cacheReadTokens": "cache_read",
    "cacheWriteTokens": "cache_write",
    "outputTokens": "output",
    "decodeTokens": "decode",
}

# An OTLP log record is only meaningful once per (session, event); batching +
# collector retries can otherwise double-count a delivery. Records that carry an
# ``event.seq`` are de-duplicated with a bounded LRU. ``event.seq``-less records
# are always counted (there is no stable identity to de-duplicate on).
_DEDUP_CAPACITY = int(os.environ.get("DSH_DEDUP_CAPACITY", "100_000"))

# Attribute names, matched on the dotted key or its leaf, that identify a record.
_ATTR_SESSION = ("session.id", "session_id", "sessionId")
_ATTR_MODEL = ("model", "model_id", "modelId", "model.name")
_ATTR_TOOL = ("tool", "tool_id", "toolId", "tool.name")
_ATTR_SEQ = ("event.seq", "event_seq", "eventSeq")

#: Text-format error bodies and their HTTP status codes.
_BAD_REQUEST = JSONResponse(
    status_code=400,
    content={"error": "payload is not a valid OTLP/HTTP JSON logs document"},
)
_UNSUPPORTED_MEDIA = JSONResponse(
    status_code=415,
    content={
        "error": (
            "this endpoint accepts OTLP/HTTP JSON only "
            "(Content-Type: application/json)"
        )
    },
)


@dataclass(frozen=True)
class _UsageObservation:
    """One numeric token reading extracted from a log record."""

    token_type: str
    value: float
    session_id: str
    model: str
    tool: str


def _join_path(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else str(key)


def _unwrap_any_value(value: Any) -> Any:
    """Return the payload of an OTLP AnyValue (proto-JSON) wrapper if present.

    When the Collector forwards logs with ``encoding: json``, ``body`` and each
    attribute value arrive as ``AnyValue`` objects such as
    ``{"stringValue": "..."}`` / ``{"intValue": "5"}`` rather than raw scalars.
    """
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        # proto-JSON encodes integers as strings ("77").
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return value["intValue"]
    if "doubleValue" in value:
        try:
            return float(value["doubleValue"])
        except (TypeError, ValueError):
            return value["doubleValue"]
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "bytesValue" in value:
        return value["bytesValue"]
    # kvlistValue / arrayValue are handled structurally by ``_iter_leaves``.
    return value


def _iter_leaves(value: Any, prefix: str = "") -> Iterator[Tuple[str, Any]]:
    """Yield ``(dotted_path, scalar)`` pairs for a (possibly wrapped) value.

    Scalar leaf values are only yielded once recursion bottoms out, so this is
    safe against deeply nested OTLP structures.
    """
    value = _unwrap_any_value(value)

    # kvlistValue: {"values": [{"key": ..., "value": ...}, ...]}
    if isinstance(value, dict) and isinstance(value.get("kvlistValue"), dict):
        kv = value["kvlistValue"].get("values")
        if isinstance(kv, list):
            for entry in kv:
                if isinstance(entry, dict) and "key" in entry:
                    yield from _iter_leaves(entry.get("value"), _join_path(prefix, entry["key"]))
        return

    # arrayValue: {"values": [...]}
    if isinstance(value, dict) and isinstance(value.get("arrayValue"), dict):
        arr = value["arrayValue"].get("values")
        if isinstance(arr, list):
            for index, item in enumerate(arr):
                yield from _iter_leaves(item, _join_path(prefix, str(index)))
        return

    if isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_leaves(child, _join_path(prefix, key))
        return

    if isinstance(value, list):
        # OTel ``repeated KeyValue`` (e.g. LogRecord.attributes) serializes as
        # an array of {"key": name, "value": <AnyValue>} objects in proto-JSON.
        if value and all(isinstance(e, dict) and "key" in e and "value" in e for e in value):
            for entry in value:
                key = str(_unwrap_any_value(entry.get("key")))
                yield from _iter_leaves(entry.get("value"), _join_path(prefix, key))
            return
        for index, child in enumerate(value):
            yield from _iter_leaves(child, _join_path(prefix, str(index)))
        return

    # A JSON string payload (the common DSH case: body = serialized usage JSON).
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, (dict, list)):
                yield from _iter_leaves(parsed, prefix)
                return
    yield prefix, value


def _leaf(value: Any) -> str:
    """Return the final path component of a dotted leaf key."""
    return value.rsplit(".", 1)[-1] if isinstance(value, str) else str(value)


def _resolve(leaves: Mapping[str, Any], aliases: Sequence[str], default: str) -> str:
    """Return the first scalar among ``leaves`` whose key matches an alias."""
    for key, val in leaves.items():
        leaf = _leaf(key)
        if key in aliases or leaf in aliases:
            if isinstance(val, (str, int, float, bool)) and val not in ("", None):
                return str(val)
    return default


def _find_leaf(leaves: Mapping[str, Any], field: str) -> Any:
    """Return the value of the first leaf whose final path component is ``field``."""
    for key, val in leaves.items():
        if _leaf(key) == field:
            return val
    return None


def extract_tokens_and_labels(record: Mapping[str, Any]) -> list[_UsageObservation]:
    """Convert one OTLP log record into its numeric usage observations.

    ``record`` follows the OTLP/HTTP JSON log schema: it holds ``body`` and
    ``attributes`` (either plain maps or OTLP ``AnyValue`` proto-JSON). The
    original ``day``-per-record label is intentionally not produced here — time
    bucketing is left to Prometheus/Grafana queries so the counter's label
    cardinality does not grow with time.
    """
    body = record.get("body")
    attributes = record.get("attributes")

    body_leaves: dict[str, Any] = {}
    if body is not None:
        for key, val in _iter_leaves(body):
            body_leaves[key] = val

    attr_leaves: dict[str, Any] = {}
    if attributes is not None:
        for key, val in _iter_leaves(attributes):
            attr_leaves[key] = val

    session_id = _resolve(attr_leaves, _ATTR_SESSION, "unknown")
    model = _resolve(attr_leaves, _ATTR_MODEL, "unknown")
    tool = _resolve(attr_leaves, _ATTR_TOOL, "unknown")

    observations: list[_UsageObservation] = []
    for field, token_type in TOKEN_FIELDS.items():
        value = _find_leaf(body_leaves, field)
        if value is None:
            value = _find_leaf(attr_leaves, field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            observations.append(
                _UsageObservation(
                    token_type=token_type,
                    value=float(value),
                    session_id=session_id,
                    model=model,
                    tool=tool,
                )
            )
    return observations


class _BoundedDeduplicator:
    """A small, memory-bounded set used to drop replayed log records.

    Only records that carry both a ``session.id`` and an ``event.seq`` yield a
    stable key worth de-duplicating; everything else passes through.
    """

    __slots__ = ("_seen", "_capacity")

    def __init__(self, capacity: int) -> None:
        self._seen: "OrderedDict[str, None]" = OrderedDict()
        self._capacity = max(0, capacity)

    def _record_key(self, record: Mapping[str, Any]) -> Optional[str]:
        attributes = record.get("attributes")
        if attributes is None:
            return None
        leaves: dict[str, Any] = {}
        for key, val in _iter_leaves(attributes):
            leaves[key] = val
        session_id = _resolve(leaves, _ATTR_SESSION, "")
        seq = _resolve(leaves, _ATTR_SEQ, "")
        if not session_id or not seq:
            return None
        return f"{session_id}:{seq}"

    def is_new(self, record: Mapping[str, Any]) -> bool:
        """Return ``True`` when the record should be counted this time."""
        if self._capacity <= 0:
            return True
        key = self._record_key(record)
        if key is None:
            return True
        if key in self._seen:
            return False
        self._seen[key] = None
        self._seen.move_to_end(key)
        while len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return True


def _iter_records(document: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Yield every ``LogRecord`` nested in an OTLP/HTTP logs JSON document."""
    for resource_log in document.get("resourceLogs") or []:
        if not isinstance(resource_log, dict):
            continue
        for scope_log in resource_log.get("scopeLogs") or []:
            if not isinstance(scope_log, dict):
                continue
            for record in scope_log.get("logRecords") or []:
                if isinstance(record, dict):
                    yield record


class _MetricsStore:
    """Owns the Prometheus counter and exposes an isolated sample for tests."""

    def __init__(self) -> None:
        self._deduplicator = _BoundedDeduplicator(_DEDUP_CAPACITY)
        self.tokens_total = Counter(
            "dsh_tokens_total",
            "Total DSH tokens consumed, by token type, session, model and tool.",
            ("type", "session_id", "model", "tool"),
        )
        self.records_received = Counter(
            "dsh_log_records_total",
            "OTLP log records received by the transformer.",
        )
        self.records_dropped = Counter(
            "dsh_log_records_dropped_total",
            "Log records skipped as duplicates or parse failures.",
        )

    def consume(self, document: Mapping[str, Any]) -> int:
        """Extract and count all observations in one OTLP logs document."""
        counted = 0
        for record in _iter_records(document):
            self.records_received.inc()
            if not self._deduplicator.is_new(record):
                self.records_dropped.inc()
                continue
            for obs in extract_tokens_and_labels(record):
                self.tokens_total.labels(
                    type=obs.token_type,
                    session_id=obs.session_id,
                    model=obs.model,
                    tool=obs.tool,
                ).inc(obs.value)
                counted += 1
        return counted

    def render(self) -> bytes:
        return generate_latest()


metrics = _MetricsStore()

app = FastAPI(
    title="DSH Token Telemetry Transformer",
    version="1.0.0",
    description=(
        "OTLP/HTTP logs -> Prometheus metrics generator for DeepSeek Harness "
        "token usage."
    ),
    docs_url=None,
    redoc_url=None,
)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "service": "dsh-token-telemetry-transformer",
        "version": app.version,
        "receive": "POST /v1/logs (OTLP/HTTP JSON)",
        "metrics": "GET /metrics (Prometheus text)",
    }


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/logs")
async def receive_logs(request: Request) -> Response:
    """Accept an OTLP/HTTP logs document and update the token counter.

    Only the JSON wire format is supported: the OTel Collector is configured to
    forward logs with ``encoding: json`` (see ``configs/otel.yaml``). Binary
    protobuf is rejected explicitly so misconfiguration is loud, not silent.
    """
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type and content_type not in ("application/json",):
        return _UNSUPPORTED_MEDIA

    try:
        payload = await request.json()
    except Exception:  # JSONDecodeError or a truncated/unexpected body.
        return _BAD_REQUEST

    if not isinstance(payload, dict):
        return _BAD_REQUEST

    started = time.perf_counter()
    counted = metrics.consume(payload)
    logger.info(
        "ingested OTLP logs document: %s observation(s) counted in %.1f ms",
        counted,
        (time.perf_counter() - started) * 1000,
    )
    return JSONResponse({"status": "ok", "observations_counted": counted})


@app.get("/metrics")
def metrics_endpoint() -> Response:
    """Expose Prometheus metrics (also the scrape target for Prometheus)."""
    return Response(content=metrics.render(), media_type=CONTENT_TYPE_LATEST)
