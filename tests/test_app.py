"""Unit and API tests for the DSH token-telemetry transformer."""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest
from prometheus_client import CONTENT_TYPE_LATEST

# Pin the pricing table the app-under-test loads (its module-level store reads
# PRICING_FILE on import). Defaulting to the repo seed keeps the deterministic
# cost fixture below stable without forcing a particular ambient environment.
_REPO_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PRICING_FILE", str(_REPO_ROOT / "configs" / "pricing.json"))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _session() -> str:
    return f"session-{uuid.uuid4().hex[:12]}"


def _log_record(
    *,
    session_id: str,
    seq: int,
    model: str = "deepseek-chat",
    tool: str = "run",
    body: Optional[dict] = None,
) -> dict:
    record: dict = {
        "timeUnixNano": "1700000000000000000",
        "body": {"stringValue": json.dumps(body or {})},
        "attributes": [
            {"key": "session.id", "value": {"stringValue": session_id}},
            {"key": "event.seq", "value": {"intValue": str(seq)}},
            {"key": "model", "value": {"stringValue": model}},
            {"key": "tool", "value": {"stringValue": tool}},
        ],
    }
    return record


def _otlp_payload(*records: dict) -> dict:
    return {
        "resourceLogs": [
            {"scopeLogs": [{"logRecords": list(records)}]}
        ]
    }


def _fetch_samples(client, metric: str):
    """Parse a Prometheus exposition response into ``(labels, value)`` list."""
    text = client.get("/metrics").text
    pattern = re.compile(rf"^{re.escape(metric)}\{{(?P<labels>.*?)\}}\s+(?P<value>[0-9.eE+-]+)\s*$", re.M)
    samples = []
    for match in pattern.finditer(text):
        labels = {}
        for part in match.group("labels").split(","):
            if not part:
                continue
            name, _, value = part.partition("=")
            labels[name.strip()] = value.strip().strip('"')
        samples.append((labels, float(match.group("value"))))
    return samples


def _counter_value(client, *, token_type: str, session_id: str) -> float:
    wanted = {
        "type": token_type,
        "session_id": session_id,
        "model": "deepseek-chat",
        "tool": "run",
    }
    for labels, value in _fetch_samples(client, "dsh_tokens_total"):
        if all(labels.get(k) == v for k, v in wanted.items()):
            return value
    return 0.0


# --------------------------------------------------------------------------- #
# Pure extraction
# --------------------------------------------------------------------------- #

def test_extract_plain_body_maps_fields():
    from transformer.app import extract_tokens_and_labels

    record = {
        "body": {
            "uncachedInputTokens": 10,
            "cacheReadTokens": 20,
            "outputTokens": 40,
        },
        "attributes": {
            "session.id": "s1",
            "model": "deepseek-reasoner",
            "tool": "search",
        },
    }
    obs = extract_tokens_and_labels(record)
    by_type = {o.token_type: o for o in obs}
    assert set(by_type) == {"input", "cache_read", "output"}
    assert by_type["input"].value == 10.0
    assert by_type["input"].session_id == "s1"
    assert by_type["input"].model == "deepseek-reasoner"
    assert by_type["input"].tool == "search"


def test_extract_nested_and_anyvalue_wrappers():
    """Token fields may be nested and arrive as OTLP AnyValue proto-JSON."""
    from transformer.app import extract_tokens_and_labels

    record = {
        "body": {
            "kvlistValue": {
                "values": [
                    {"key": "usage", "value": {"kvlistValue": {"values": [
                        {"key": "outputTokens", "value": {"intValue": "77"}},
                    ]}}},
                ]
            }
        },
        "attributes": {
            "session.id": {"stringValue": "s2"},
            "event.seq": {"intValue": "3"},
        },
    }
    obs = extract_tokens_and_labels(record)
    assert len(obs) == 1
    assert obs[0].token_type == "output"
    assert obs[0].value == 77.0
    assert obs[0].session_id == "s2"


def test_extract_ignores_zero_negative_and_missing():
    from transformer.app import extract_tokens_and_labels

    record = {
        "body": {"uncachedInputTokens": 0, "cacheReadTokens": -5},
        "attributes": {},
    }
    assert extract_tokens_and_labels(record) == []
    assert extract_tokens_and_labels({"body": {}, "attributes": {}}) == []


def test_extract_unknown_dimensions_default_to_unknown():
    from transformer.app import extract_tokens_and_labels

    obs = extract_tokens_and_labels(
        {"body": {"outputTokens": 3}, "attributes": {}}
    )
    assert obs[0].session_id == "unknown"
    assert obs[0].model == "unknown"
    assert obs[0].tool == "unknown"


def test_extract_proto_json_repeated_keyvalue_attributes():
    """OTel serializes LogRecord.attributes as [{key, value}, ...] in JSON."""
    from transformer.app import extract_tokens_and_labels

    record = {
        "body": {"stringValue": json.dumps({"outputTokens": 42})},
        "attributes": [
            {"key": "session.id", "value": {"stringValue": "s-proto"}},
            {"key": "event.seq", "value": {"intValue": "7"}},
            {"key": "model", "value": {"stringValue": "deepseek-reasoner"}},
            {"key": "tool", "value": {"stringValue": "browser"}},
        ],
    }
    obs = extract_tokens_and_labels(record)
    assert len(obs) == 1
    assert obs[0].token_type == "output"
    assert obs[0].value == 42.0
    assert obs[0].session_id == "s-proto"
    assert obs[0].model == "deepseek-reasoner"
    assert obs[0].tool == "browser"


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_post_logs_increments_counter(client):
    session_id = _session()
    payload = _otlp_payload(
        _log_record(
            session_id=session_id,
            seq=1,
            body={"uncachedInputTokens": 100, "outputTokens": 50},
        )
    )

    response = client.post("/v1/logs", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["observations_counted"] == 2

    assert _counter_value(client, token_type="input", session_id=session_id) == 100.0
    assert _counter_value(client, token_type="output", session_id=session_id) == 50.0


def test_duplicate_record_is_counted_once(client):
    """Replayed (session.id, event.seq) must not double-count."""
    session_id = _session()
    payload = _otlp_payload(
        _log_record(session_id=session_id, seq=1, body={"outputTokens": 30})
    )

    assert client.post("/v1/logs", json=payload).status_code == 200
    assert client.post("/v1/logs", json=payload).status_code == 200

    assert _counter_value(client, token_type="output", session_id=session_id) == 30.0

    # A different seq in the same session is a distinct record -> counted.
    other = _otlp_payload(
        _log_record(session_id=session_id, seq=2, body={"outputTokens": 5})
    )
    assert client.post("/v1/logs", json=other).status_code == 200
    assert _counter_value(client, token_type="output", session_id=session_id) == 35.0


def test_metrics_content_type_and_family(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    text = response.text
    assert "# HELP dsh_tokens_total" in text
    assert "# TYPE dsh_tokens_total counter" in text


def test_invalid_body_returns_400(client):
    assert client.post("/v1/logs", content=b"not-json", headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/v1/logs", json=[1, 2, 3]).status_code == 400


def test_non_json_content_type_returns_415(client):
    response = client.post(
        "/v1/logs",
        content=b"\x08\x01",
        headers={"content-type": "application/x-protobuf"},
    )
    assert response.status_code == 415


def test_metrics_isolation_across_sessions(client):
    a, b = _session(), _session()
    client.post("/v1/logs", json=_otlp_payload(_log_record(session_id=a, seq=1, body={"outputTokens": 10})))
    client.post("/v1/logs", json=_otlp_payload(_log_record(session_id=b, seq=1, body={"outputTokens": 20})))
    assert _counter_value(client, token_type="output", session_id=a) == 10.0
    assert _counter_value(client, token_type="output", session_id=b) == 20.0


# --------------------------------------------------------------------------- #
# Edge cost (stage 1)
# --------------------------------------------------------------------------- #

def _cost_by(client, session_id: str) -> dict:
    out: dict = {}
    for labels, value in _fetch_samples(client, "dsh_cost_usd_total"):
        if labels.get("session_id") == session_id:
            out[labels["type"]] = out.get(labels["type"], 0.0) + value
    return out


def _savings_for_session(client, session_id: str) -> float:
    return sum(
        value for labels, value in _fetch_samples(client, "dsh_cache_savings_usd_total")
        if labels.get("session_id") == session_id
    )


def _nanos(dt_utc: datetime) -> str:
    return str(int(dt_utc.timestamp() * 1e9))


def _plain_counter_value(client, metric: str) -> float:
    """Read a single label-less counter (e.g. dsh_cost_unknown_model_total)."""
    text = client.get("/metrics").text
    match = re.search(rf"^{re.escape(metric)}\s+([0-9.eE+-]+)\s*$", text, re.M)
    return float(match.group(1)) if match else 0.0


def test_cost_counter_deterministic_fixture(client):
    """Stage-1 deterministic fixture: Mon 2026-09-07 12:00Z, deepseek-chat.

    uncachedInputTokens=1000, cacheReadTokens=1000, cacheWriteTokens=0,
    outputTokens=2000, decodeTokens=900 (decode must NOT be billed).
    off_peak flash prices .22/.007/.66 => cost .001547, savings .000213.
    """
    session_id = _session()
    record = _log_record(
        session_id=session_id,
        seq=1,
        body={
            "uncachedInputTokens": 1000,
            "cacheReadTokens": 1000,
            "cacheWriteTokens": 0,
            "outputTokens": 2000,
            "decodeTokens": 900,
        },
    )
    record["timeUnixNano"] = _nanos(datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc))

    response = client.post("/v1/logs", json=_otlp_payload(record))
    assert response.status_code == 200

    by_type = _cost_by(client, session_id)
    assert by_type["input_miss"] == pytest.approx(0.00022)
    assert by_type["input_hit"] == pytest.approx(0.000007)
    assert by_type["output"] == pytest.approx(0.00132)
    # decode is never billed.
    assert "decode" not in by_type
    assert sum(by_type.values()) == pytest.approx(0.001547)

    # Labels on the emitted series.
    matched = [
        labels for labels, _ in _fetch_samples(client, "dsh_cost_usd_total")
        if labels.get("session_id") == session_id and labels.get("type") == "input_miss"
    ]
    assert matched
    assert matched[0]["model"] == "deepseek-v4-flash"
    assert matched[0]["tool"] == "run"
    assert matched[0]["effort"] == "low"
    assert matched[0]["period"] == "off_peak"
    assert matched[0]["price_version"] == "v1"

    assert _savings_for_session(client, session_id) == pytest.approx(0.000213)


def test_cost_counter_peak_period_uses_peak_price(client):
    """Tuesday 2026-09-08 08:00Z is peak => flash output billed at 1.32/1M."""
    session_id = _session()
    record = _log_record(
        session_id=session_id,
        seq=1,
        body={"outputTokens": 1_000_000},
    )
    record["timeUnixNano"] = _nanos(datetime(2026, 9, 8, 8, 0, tzinfo=timezone.utc))
    client.post("/v1/logs", json=_otlp_payload(record))

    by_type = _cost_by(client, session_id)
    assert by_type["output"] == pytest.approx(1.32)
    matched = [
        labels for labels, _ in _fetch_samples(client, "dsh_cost_usd_total")
        if labels.get("session_id") == session_id and labels.get("type") == "output"
    ]
    assert matched
    assert matched[0]["period"] == "peak"
    assert matched[0]["effort"] == "low"
    assert matched[0]["model"] == "deepseek-v4-flash"
    assert matched[0]["price_version"] == "v1"


def test_cost_unknown_model_increments_and_not_billed(client):
    before = _plain_counter_value(client, "dsh_cost_unknown_model_total")
    session_id = _session()
    record = _log_record(
        session_id=session_id,
        seq=1,
        model="not-a-known-route",
        body={"outputTokens": 10},
    )
    record["timeUnixNano"] = _nanos(datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc))
    assert client.post("/v1/logs", json=_otlp_payload(record)).status_code == 200

    after = _plain_counter_value(client, "dsh_cost_unknown_model_total")
    assert after == before + 1
    # Unknown model is unbilled -> no cost series for this session.
    assert _cost_by(client, session_id) == {}


def test_cost_metrics_families_exposed(client):
    text = client.get("/metrics").text
    for metric in (
        "dsh_cost_usd_total",
        "dsh_cache_savings_usd_total",
        "dsh_cost_unknown_model_total",
    ):
        assert f"# TYPE {metric} counter" in text


# --------------------------------------------------------------------------- #
# Session result / effectiveness (stage 4)
# --------------------------------------------------------------------------- #

def _metric_value_for(client, metric: str, wanted: dict) -> float:
    """Return the first sample value for ``metric`` whose labels match ``wanted``."""
    for labels, value in _fetch_samples(client, metric):
        if all(labels.get(k) == v for k, v in wanted.items()):
            return value
    return 0.0


def test_terminal_record_emits_result_series(client):
    """A session terminal event counts as outcome + work + quality (not usage)."""
    session_id = _session()
    # Includes outputTokens on purpose: a terminal record must NOT become usage.
    record = _log_record(
        session_id=session_id,
        seq=1,
        body={
            "status": "completed",
            "quality_estimate": 0.8,
            "files_changed": 3,
            "lines_added": 42,
            "lines_deleted": 7,
            "methods_added": 2,
            "outputTokens": 5000,  # must be ignored: terminal records are not usage
        },
    )
    record["timeUnixNano"] = _nanos(datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc))
    response = client.post("/v1/logs", json=_otlp_payload(record))
    assert response.status_code == 200

    base = {
        "session_id": session_id,
        "model": "deepseek-v4-flash",  # normalised tariff, joins with cost
        "tool": "run",
        "effort": "low",
    }
    outcome = dict(base, status="completed")
    assert _metric_value_for(client, "dsh_session_outcome_total", outcome) == 1.0
    assert _metric_value_for(client, "dsh_result_files_changed_total", base) == 3.0
    assert _metric_value_for(client, "dsh_result_lines_added_total", base) == 42.0
    assert _metric_value_for(client, "dsh_result_lines_deleted_total", base) == 7.0
    assert _metric_value_for(client, "dsh_result_methods_added_total", base) == 2.0
    assert _metric_value_for(client, "dsh_result_quality", base) == 0.8

    # Terminal record is NOT usage -> no token series for this session.
    assert _metric_value_for(
        client, "dsh_tokens_total", {"type": "output", "session_id": session_id}
    ) == 0.0


def test_usage_without_status_creates_no_result_series(client):
    """A plain usage record (no status) must not mint any result series."""
    session_id = _session()
    record = _log_record(
        session_id=session_id,
        seq=1,
        body={"uncachedInputTokens": 100, "outputTokens": 50},
    )
    record["timeUnixNano"] = _nanos(datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc))
    assert client.post("/v1/logs", json=_otlp_payload(record)).status_code == 200

    base = {"session_id": session_id, "model": "deepseek-v4-flash", "tool": "run", "effort": "low"}
    assert _metric_value_for(
        client, "dsh_session_outcome_total", dict(base, status="completed")
    ) == 0.0
    assert _metric_value_for(client, "dsh_result_files_changed_total", base) == 0.0
    assert _metric_value_for(client, "dsh_result_quality", base) == 0.0
