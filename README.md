# DeepSeek Harness Telemetry → Local Grafana + Prometheus

A fully **local** observability stack that captures DeepSeek Harness (DSH)
session telemetry, converts the OTLP **logs** DSH ships into Prometheus token
metrics, and visualises them in Grafana — sliced by token type, session, model
and tool.

No external SaaS is used. The OTel Collector is the only network receiver;
everything else runs inside Docker Compose.

## Architecture

```
DSH ──OTLP/HTTP logs──▶ otel-collector:4318 ──OTLP/HTTP JSON──▶ transformer:8000
                                                                      │ (log→metric)
Grafana:3000 ◀──PromQL── Prometheus:9090 ◀──scrape /metrics──┘
```

- `otel-collector` receives DSH's OTLP/HTTP logs on `4318` and forwards them to
  the transformer over HTTP. The collector cannot mint Prometheus counters out
  of log bodies, so it forwards with `encoding: json` to the log→metric service.
- `transformer` is the only component with business logic. It parses each log
  record, extracts the numeric usage fields, de-duplicates by
  `(session.id, event.seq)`, and increments a `dsh_tokens_total` counter
  labelled by `type`, `session_id`, `model`, `tool`. It serves `/metrics` for
  Prometheus.
- `prometheus` scrapes the transformer every 10s and stores the counters.
- `grafana` is pre-provisioned with a Prometheus data source and an auto-imported
  **DSH Token Usage** dashboard.

> Token usage arrives as **logs**, not metric series. Numeric Prometheus
> token metrics are produced by the transformer (the log→metric transform), not
> by the collector.

## Quick Start

```bash
docker compose up -d --build
```

### Point DSH at the stack (restart-safe)

```bash
export DSH_TELEMETRY_MODE=FULL
export DSH_TELEMETRY_OTLP_URL=http://localhost:4318/v1/logs
# start / restart DeepSeek Harness so it picks up the environment
```

- `DSH_TELEMETRY_MODE` → `FULL` (live records) or `FEEDBACK_ONLY`.
- Any non-empty `DSH_TELEMETRY_DISABLED` opts out.
- The URL host port `4318` maps to the collector's OTLP/HTTP receiver.

Keep telemetry local: DSH `FULL` mode exports raw record content, so do **not**
point this URL at an untrusted remote endpoint.

### Verify the pipeline

```bash
# Collector is up and receiving on the OTLP/HTTP port:
docker compose ps

# Token series are exposed by the transformer:
curl http://localhost:8002/metrics | grep ^dsh_tokens_total

# Prometheus has scraped them:
open http://localhost:9090   # query: dsh_tokens_total

# Grafana dashboard:
open http://localhost:3000   # admin / admin  → "DSH Token Usage"
```

## Services & Ports

| Service         | Port (host) | Purpose                                   |
|-----------------|-------------|-------------------------------------------|
| otel-collector  | 4318        | OTLP/HTTP logs receiver (DSH target)      |
| transformer     | 8002        | Metrics inspection (`/metrics`)           |
| prometheus      | 9090        | Scrape + PromQL                           |
| grafana         | 3000        | Dashboards (admin/admin)                  |

## File Layout

| Path                                                 | Purpose                                       |
|------------------------------------------------------|-----------------------------------------------|
| `docker-compose.yml`                                 | Orchestration, volumes, health/restart policy |
| `configs/otel.yaml`                                  | Collector: receive OTLP, forward JSON to transformer |
| `configs/prometheus.yml`                             | Scrape target (`transformer:8000`)            |
| `configs/grafana/datasources/prometheus.yaml`        | Provisioned Prometheus data source            |
| `configs/grafana/dashboards/dashboards.yaml`         | Dashboard auto-provisioning provider          |
| `configs/grafana/dashboards/dsh-dashboard.json`      | "DSH Token Usage" dashboard                   |
| `transformer/Dockerfile`                             | Transformer image (non-root)                  |
| `transformer/app.py`                                 | OTLP logs → Prometheus counter transformer    |
| `transformer/requirements.txt`                       | Pinned runtime dependencies                   |
| `transformer/requirements-dev.txt`                   | Pinned test dependencies                      |
| `tests/`                                             | Transformer test suite                        |

## Token fields mapped to `dsh_tokens_total`

| DSH field            | `type` label   |
|----------------------|----------------|
| `uncachedInputTokens`| `input`        |
| `cacheReadTokens`    | `cache_read`   |
| `cacheWriteTokens`   | `cache_write`  |
| `outputTokens`       | `output`       |
| `decodeTokens`       | `decode`       |

Fields are matched by **leaf key name**, so they are found whether DSH records
them flat or nested. Zero/negative values are ignored; the counter is only ever
incremented by positive counts.

## Development

Run the test suite:

```bash
python3 -m venv .venv && .venv/bin/pip install -r transformer/requirements-dev.txt
.venv/bin/python -m pytest tests/
```

The tests cover field extraction (flat, nested, proto-JSON `AnyValue`), label
resolution from OTLP `repeated KeyValue` attributes, API behaviour, error
handling, de-duplication and per-session metric isolation.

Rebuild after changing the transformer or configs:

```bash
docker compose up -d --build
```

## Requirements

- Docker Engine 20.10+ and Docker Compose v2
- DeepSeek Harness with the `@deepseek-ai/dsh-session-telemetry-otel` module
  mounted (env knobs above)
