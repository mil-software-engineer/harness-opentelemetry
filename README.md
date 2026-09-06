# DSH Telemetry → Local Grafana + Prometheus (+ Loki)

A fully **local** observability stack for DeepSeek Harness (DSH): it turns the
OTLP **logs** DSH emits into Prometheus token/cost metrics and stores the raw
logs in Loki — all visualised in Grafana. No external SaaS.

## Architecture

```
DSH ──OTLP/HTTP logs (protobuf)──▶ otel-collector:4318
                                        │
                    ┌───────────────────┴───────────────────┐
                    ▼                                       ▼
            otlp_http/transformer (JSON)             otlp_http/loki (JSON)
                    ▼                                       ▼
              transformer:8000                          loki:3100/otlp
              tokens + edge-cost + result               raw logs / context
                    │
                    ▼
   Prometheus:9090 ◀── scrape transformer:8000 ──▶ Grafana:3000 (Prom + Loki)
```

- **otel-collector** receives DSH OTLP/HTTP logs on `4318` and re-exports the
  stream twice in parallel: as OTLP/HTTP JSON to the transformer, and to Loki.
- **transformer** (FastAPI; the only component with logic) parses each log
  record, de-duplicates by `(session.id, event.seq)`, and produces metrics:
  - token usage (`dsh_tokens_total`),
  - **edge cost** in USD (`dsh_cost_usd_total`, `dsh_cache_savings_usd_total`,
    `dsh_cost_unknown_model_total`) from a versioned pricing table,
  - session **result** metrics (`dsh_session_outcome_total`,
    `dsh_result_*_total`, `dsh_result_quality`) from a session terminal event.
- **prometheus** scrapes the transformer every 10s and evaluates alert rules.
- **loki** (single-binary) stores the duplicated raw log stream on disk.
- **grafana** is pre-provisioned with Prometheus + Loki sources and four
  auto-imported dashboards.

> Token usage arrives as **logs**, not metric series. Numeric/cost/result
> metrics are produced by the transformer (log→metric), never by the collector.

## Services & ports

| Service        | Host port | Purpose                                     |
|----------------|-----------|---------------------------------------------|
| otel-collector | 4318      | OTLP/HTTP logs receiver (DSH target)        |
| transformer    | 8002      | `/metrics` inspection (intake is on :8000)  |
| prometheus     | 9090      | Scrape + PromQL + alert rules               |
| loki           | 3100      | Raw log store (OTLP at `/otlp`)             |
| grafana        | 3000      | Dashboards (admin/admin)                    |

## Metrics emitted by the transformer

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `dsh_tokens_total` | counter | type, session_id, model, tool | tokens; `model` = route (e.g. `deepseek-chat`) |
| `dsh_log_records_total` / `dsh_log_records_dropped_total` | counter | — | records received / dropped as duplicates |
| `dsh_cost_usd_total` | counter | type, session_id, model, tool, effort, period, price_version | USD billed at ingest; `model` = tariff (e.g. `deepseek-v4-flash`) |
| `dsh_cache_savings_usd_total` | counter | session_id, model, tool, effort, period, price_version | USD saved by cache hits vs miss pricing |
| `dsh_cost_unknown_model_total` | counter | — | unbilled records with an unmapped model route |
| `dsh_session_outcome_total` | counter | status, session_id, model, tool, effort | terminal events (completed/failed/error) |
| `dsh_result_files_changed_total`, `dsh_result_lines_added_total`, `dsh_result_lines_deleted_total`, `dsh_result_methods_added_total` | counter | session_id, model, tool, effort | result work amounts |
| `dsh_result_quality` | gauge | session_id, model, tool, effort | quality estimate 0..1 (when present) |

**Token field → billing slot** (`configs/pricing.json`):
`uncachedInputTokens→input_miss`, `cacheReadTokens→input_hit`,
`cacheWriteTokens→input_write`, `outputTokens→output`. `decodeTokens` is counted
in `dsh_tokens_total` but deliberately **not billed** (relation to
`output`/reasoning unconfirmed). Fields are matched by **leaf key name** (flat or
nested); zero/negative values are ignored. Cost is decided by the record
timestamp (peak/off-peak UTC) and the active `effective_from` pricing row. A
result record (leaf `status` ∈ completed/failed/error) is never counted as
token/cost usage.

## Grafana dashboards (auto-provisioned)

- **DSH Token Usage** — tokens/cost by type, model, session, tool.
- **DSH Efficiency** — cache flow, cache-hit coefficient, context/prompt
  hotspots, cost/output by effort.
- **DSH Effectiveness** — session outcomes, success rate, cost-per-successful-
  session, cost/tokens-per-file, quality.
- **DSH Session Logs** (Loki) — search raw logs / session context by session.

Prometheus alert rules (`configs/prometheus-alerts.yml`, local only, no
notifier): `DSHHighSessionCost`, `DSHUnknownModelGrowing`, `DSHLowCacheHitRate`.

## Quick start

```bash
docker compose up -d --build
```

Point DSH at the stack (restart-safe):

```bash
export DSH_TELEMETRY_MODE=FULL
export DSH_TELEMETRY_OTLP_URL=http://localhost:4318/v1/logs
# start / restart DeepSeek Harness to pick up the environment
```

- `DSH_TELEMETRY_MODE` → `FULL` (live) / `FEEDBACK_ONLY`; any non-empty
  `DSH_TELEMETRY_DISABLED` opts out.
- `FULL` exports raw record content — keep the URL on this **local** collector.

## Verify

```bash
docker compose ps                     # all five services Up
curl http://localhost:8002/metrics | grep ^dsh_   # token/cost/result series
open http://localhost:9090             # Prometheus: target UP, rules loaded
open http://localhost:3000             # Grafana dashboards (admin/admin)
# Raw logs also land in Loki:
curl -G http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode 'query={service_name="deepseek-harness"}'
```

## File layout

| Path | Purpose |
|------|---------|
| `docker-compose.yml` | Orchestration (otel-collector, transformer, prometheus, loki, grafana), volumes, health/restart |
| `configs/otel.yaml` | Collector: receive OTLP, forward JSON to transformer + Loki |
| `configs/pricing.json` | Versioned pricing table, peak/off-peak, route→model map |
| `configs/loki.yaml` | Single-binary Loki storage config |
| `configs/prometheus.yml`, `configs/prometheus-alerts.yml` | Scrape config + alert rules |
| `configs/grafana/datasources/*.yaml` | Provisioned Prometheus + Loki sources |
| `configs/grafana/dashboards/*` | Provider + four dashboards |
| `transformer/Dockerfile` | Non-root image, healthcheck |
| `transformer/app.py` | OTLP logs → tokens/cost/result metrics |
| `transformer/pricing.py` | Pure pricing / peak / model-normalisation logic |
| `transformer/requirements.txt`, `requirements-dev.txt` | Pinned runtime / test deps |
| `tests/` | pytest suite (`test_app.py`, `test_pricing.py`) |
| `docs/` | Architecture, spec, runbook, dev guide |

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r transformer/requirements-dev.txt
.venv/bin/python -m pytest tests/
docker compose up -d --build   # after changing transformer/ or configs/
```

Note: `model` on `dsh_tokens_total` is the telemetry **route**
(`deepseek-chat`/`deepseek-reasoner`); on cost/result counters it is the
normalised **tariff** name (`deepseek-v4-flash`/`-pro`). Join across them via
`route_map` in `configs/pricing.json` when needed.

## Requirements

- Docker Engine 20.10+ and Docker Compose v2
- DSH with the `@deepseek-ai/dsh-session-telemetry-otel` module mounted
