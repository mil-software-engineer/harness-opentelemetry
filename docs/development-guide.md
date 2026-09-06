# Development Guide — DSH Telemetry Export

## 1. Local environment

- Python 3.11+ (the transformer image uses `python:3.11-slim`)
- Docker Engine 20.10+ and Docker Compose v2
- Git

## 2. Project layout

```
docker-compose.yml                       # orchestration
configs/otel.yaml                        # collector config
configs/prometheus.yml                   # scrape config
configs/grafana/{datasources,dashboards}/ # Grafana provisioning
transformer/Dockerfile                   # image build
transformer/app.py                       # FastAPI log→metric transformer
transformer/requirements.txt             # pinned runtime deps
transformer/requirements-dev.txt         # pinned test deps
tests/                                   # pytest suite
docs/                                    # this documentation set
```

The transformer is a single FastAPI application. Key functions in
`transformer/app.py`:

- `_iter_leaves` / `_unwrap_any_value` — walk OTLP proto-JSON
  (`AnyValue` wrappers, `repeated KeyValue` attributes, JSON-string bodies).
- `extract_tokens_and_labels(record)` — pure function returning numeric usage
  observations with resolved labels.
- `_BoundedDeduplicator` — LRU that drops replayed `(session.id, event.seq)`.
- Endpoints: `POST /v1/logs`, `GET /metrics`, `GET /healthz`, `GET /`.

## 3. Setting up for development

```bash
python3 -m venv .venv
.venv/bin/pip install -r transformer/requirements-dev.txt
```

The dev requirements file includes the pinned runtime deps plus `pytest` and
`httpx`.

## 4. Testing

```bash
.venv/bin/python -m pytest tests/
```

The suite covers field extraction (flat, nested, proto-JSON `AnyValue` and
`repeated KeyValue` attributes), label resolution and fallbacks, zero/negative
value handling, de-duplication, the `POST /v1/logs` / `GET /metrics` /
`GET /healthz` endpoints, and error handling (malformed JSON → 400, non-JSON
content-type → 415).

## 5. Running the transformer standalone

```bash
.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000   # from transformer/
# or
cd transformer && ../.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
```

Send a sample OTLP/HTTP JSON document (proto-JSON shape):

```bash
curl -X POST http://localhost:8000/v1/logs \
  -H "Content-Type: application/json" \
  -d '{
    "resourceLogs": [{
      "scopeLogs": [{
        "logRecords": [{
          "body": {"stringValue": "{\"uncachedInputTokens\":10,\"outputTokens\":7}"},
          "attributes": [
            {"key": "session.id", "value": {"stringValue": "test"}},
            {"key": "event.seq",  "value": {"intValue": "1"}},
            {"key": "model",      "value": {"stringValue": "deepseek-chat"}},
            {"key": "tool",       "value": {"stringValue": "coding"}}
          ]
        }]
      }]
    }]
  }'
curl http://localhost:8000/metrics | grep ^dsh_tokens_total
```

## 6. Building the image

```bash
docker compose build transformer     # or: docker compose up -d --build
docker compose up -d
```

The Docker image runs as the non-root user `appuser` and exposes a
`HEALTHCHECK` on `GET /healthz`.

## 7. Contributing

- Open a pull request against `main`.
- Keep changes scoped: if behaviour (ports/topology/metric labels) changes,
  update `docker-compose.yml`, `configs/*` and the docs together.
- Run `python -m pytest tests/` before finishing.
- Use clear commit messages and PEP 8, typed Python.
