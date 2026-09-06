# Technical Specification — DSH Telemetry Export

## 1. Architecture overview

A pipeline of four containerised components:

1. **DSH** pushes OTLP/HTTP **logs** (protobuf) to `otel-collector:4318/v1/logs`.
2. **otel-collector** batches the logs and forwards them as OTLP/HTTP **JSON**
   to the transformer.
3. **transformer** converts each log record into the `dsh_tokens_total`
   Prometheus counter and exposes `/metrics`.
4. **prometheus** scrapes the transformer; **grafana** queries Prometheus.

> DSH ships logs only. The transformer is the log→metric generator; the
> collector cannot mint counters from log bodies.

## 2. Component details

### 2.1 otel-collector

- Image: `otel/opentelemetry-collector-contrib:latest`
- Config: `configs/otel.yaml`
  - Receiver `otlp` (HTTP) on `0.0.0.0:4318` — host port `4318`.
  - Processors: `memory_limiter`, `batch`.
  - Exporter `otlp_http/transformer`: `endpoint: http://transformer:8000`,
    `encoding: json`, `compression: none` (the exporter appends `/v1/logs`).
  - Exporter `debug` (basic) for local inspection; `health_check` extension.
  - Pipeline `logs`: `otlp → memory_limiter → batch → otlp_http/transformer, debug`.

### 2.2 transformer

- Python 3.11 + FastAPI; `transformer/Dockerfile`, non-root `appuser`.
- Image built as `dsh-telemetry-transformer:local`; runs on container port `8000`
  (single port serves both log intake and metrics).
- Host mapping `8002:8000` is a convenience for manual inspection.
- Endpoints:
  - `GET /` — service info.
  - `GET /healthz` — health probe (`{"status":"ok"}`).
  - `POST /v1/logs` — accept an OTLP/HTTP JSON logs document (415 for protobuf,
    400 for malformed payload).
  - `GET /metrics` — Prometheus text exposition.
- Metric definition:
  - `dsh_tokens_total{counter}` labelled by `type`, `session_id`, `model`,
    `tool`. Description: total DSH tokens consumed.
  - Token types: `input`, `cache_read`, `cache_write`, `output`, `decode`.
  - Supporting counters: `dsh_log_records_total`, `dsh_log_records_dropped_total`.
- De-duplication: bounded LRU keyed by `(session.id, event.seq)`; capacity via
  `DSH_DEDUP_CAPACITY` (default `100000`).

### 2.3 prometheus

- Image: `prom/prometheus:latest`
- Config: `configs/prometheus.yml` — job `transformer`, scrape interval 10s,
  target `transformer:8000`, `metrics_path: /metrics`.
- Persistent volume `prometheus_data`. Host port `9090`.

### 2.4 grafana

- Image: `grafana/grafana:latest`
- Provisioning (read-only mounts):
  - Datasource: `configs/grafana/datasources/prometheus.yaml` → `Prometheus`
    (`uid: prometheus`, default) → `http://prometheus:9090`.
  - Dashboard provider: `configs/grafana/dashboards/dashboards.yaml` →
    imports every `*.json` in the folder, including `dsh-dashboard.json`
    (**DSH Token Usage**, `uid: dsh-token-usage`).
- Persistent volume `grafana_data`. Host port `3000` (admin/admin).

## 3. Data model

### 3.1 OTLP/HTTP JSON log record (as the collector forwards it)

The collector emits proto-JSON: `attributes` is an OTel `repeated KeyValue`
array, and `body` is an OTLP `AnyValue` (often a JSON string containing the
usage fields).

```json
{
  "timeUnixNano": "1757152345123456789",
  "severityNumber": 9,
  "severityText": "INFO",
  "body": {
    "stringValue": "{\"uncachedInputTokens\":123,\"outputTokens\":89}"
  },
  "attributes": [
    {"key": "session.id", "value": {"stringValue": "abc-123"}},
    {"key": "event.seq",  "value": {"intValue": "1"}},
    {"key": "model",      "value": {"stringValue": "deepseek-reasoner"}},
    {"key": "tool",       "value": {"stringValue": "agent"}}
  ]
}
```

The transformer also tolerates the plain-map form (`attributes` as an object,
`body` as a JSON object or raw map). Token fields are matched by **leaf key
name**, so they are found flat or nested, in `body` or `attributes`; zero and
negative values are ignored.

### 3.2 Metric exposure

Each positive numeric token field increments `dsh_tokens_total` with the
corresponding labels. Example — a record with `outputTokens=89` for session
`abc-123` on model `deepseek-reasoner` / tool `agent`:

```
dsh_tokens_total{type="output", session_id="abc-123",
                 model="deepseek-reasoner", tool="agent"} 89
```

### 3.3 Batching

The collector uses `memory_limiter` + `batch` to smooth bursts. The transformer
counts each record independently; the bounded LRU prevents double counting on
retries.

## 4. Configuration files

| File                                                   | Purpose                                        |
|--------------------------------------------------------|------------------------------------------------|
| `docker-compose.yml`                                   | Orchestration, volumes, health/restart policy |
| `configs/otel.yaml`                                    | Collector config (OTLP receive → JSON forward)|
| `configs/prometheus.yml`                               | Scrape target and interval                     |
| `configs/grafana/datasources/prometheus.yaml`          | Provisioned Prometheus data source             |
| `configs/grafana/dashboards/dashboards.yaml`           | Dashboard auto-provisioning provider           |
| `configs/grafana/dashboards/dsh-dashboard.json`        | "DSH Token Usage" dashboard                    |
| `transformer/Dockerfile`                               | Transformer image build                        |
| `transformer/app.py`                                   | FastAPI log→metric transformer                 |
| `transformer/requirements.txt`                         | Pinned runtime deps                            |
| `transformer/requirements-dev.txt`                     | Pinned test deps                               |
| `tests/`                                               | pytest suite for the transformer               |
| `docs/*.md`                                            | This documentation set                         |

## 5. Ports

| Host | Container | Service     | Purpose                              |
|------|-----------|-------------|--------------------------------------|
| 4318 | 4318      | otel-collector | OTLP/HTTP logs receiver (DSH target) |
| 8002 | 8000      | transformer  | Manual `/metrics` (log intake also on 8000) |
| 9090 | 9090      | prometheus   | PromQL / targets UI                  |
| 3000 | 3000      | grafana      | Dashboard UI (admin/admin)           |

## 6. Security considerations

- All traffic stays on the host; the collector/transformer are not exposed
  beyond `4318`/`8002`/`9090`/`3000`.
- DSH `FULL` mode exports raw record content — keep it pointed at this local
  collector only.
- The transformer persists nothing; all state is in-memory and reset on restart.
