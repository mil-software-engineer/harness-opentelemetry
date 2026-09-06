"""Unit and API tests for the DSH token-telemetry transformer."""

import json
import re
import uuid
from typing import Optional

from prometheus_client import CONTENT_TYPE_LATEST


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
