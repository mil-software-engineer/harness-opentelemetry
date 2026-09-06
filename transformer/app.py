import json
import re
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from prometheus_client import Counter, generate_latest, REGISTRY, CONTENT_TYPE_LATEST
from starlette.responses import Response

app = FastAPI()

# Metric: total token count with labels
token_counter = Counter(
    'dsh_tokens_total',
    'Total tokens consumed, split by type, session, model, tool, and day',
    ['type', 'session_id', 'model', 'tool', 'day']
)

# Deduplication set (session.id + event.seq) – optional
seen_records = set()

# Token fields mapping to Prometheus types
TOKEN_FIELDS = {
    'uncachedInputTokens': 'input',
    'cacheReadTokens': 'cache_read',
    'cacheWriteTokens': 'cache_write',
    'outputTokens': 'output',
    'decodeTokens': 'decode',
}


def extract_tokens_and_labels(record: dict) -> list:
    """Extracts numeric tokens and labels from a single log record."""
    body = record.get('body', {})
    attributes = record.get('attributes', {})
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            body = {}

    combined = {}
    if isinstance(body, dict):
        combined.update(body)
    if isinstance(attributes, dict):
        combined.update(attributes)

    session_id = attributes.get('session.id', 'unknown')
    model = attributes.get('model', 'unknown')
    tool = attributes.get('tool', 'unknown')
    timestamp_ns = record.get('timeUnixNano') or record.get('observedTimeUnixNano')
    if timestamp_ns:
        dt = datetime.fromtimestamp(int(timestamp_ns) / 1_000_000_000, tz=timezone.utc)
        day = dt.strftime('%Y-%m-%d')
    else:
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    results = []
    for field, token_type in TOKEN_FIELDS.items():
        if field in combined and isinstance(combined[field], (int, float)):
            value = combined[field]
            if value > 0:
                results.append((token_type, value, session_id, model, tool, day))
    return results


@app.post("/v1/logs")
async def receive_logs(request: Request):
    """Accepts OTLP/HTTP logs in JSON format."""
    data = await request.json()
    resource_logs = data.get('resourceLogs', [])
    for rl in resource_logs:
        scope_logs = rl.get('scopeLogs', [])
        for sl in scope_logs:
            for record in sl.get('logRecords', []):
                # Deduplication (optional)
                # seq = record.get('attributes', {}).get('event.seq')
                # sid = record.get('attributes', {}).get('session.id')
                # if sid and seq is not None:
                #     key = f"{sid}:{seq}"
                #     if key in seen_records:
                #         continue
                #     seen_records.add(key)

                items = extract_tokens_and_labels(record)
                for token_type, value, session_id, model, tool, day in items:
                    token_counter.labels(
                        type=token_type,
                        session_id=session_id,
                        model=model,
                        tool=tool,
                        day=day
                    ).inc(value)

    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    """Endpoint for Prometheus."""
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)