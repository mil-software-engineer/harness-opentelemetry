# Technical Specification — DSH Telemetry Export

## 1. Architecture overview

A pipeline of five containerised components:

1. **DSH** pushes OTLP/HTTP **logs** (protobuf) to `otel-collector:4318/v1/logs`.
2. **otel-collector** batches and re-exports the stream **twice**: as OTLP/HTTP
   JSON to the transformer, and to Loki.
3. **transformer** converts each record into Prometheus token/cost/result
   metrics and exposes `/metrics`.
4. **prometheus** scrapes the transformer; **grafana** queries Prometheus and
   Loki (raw logs) and renders dashboards.

## 2. Component details

### 2.1 otel-collector
- Image `otel/opentelemetry-collector-contrib:latest`; config `configs/otel.yaml`.
- Receiver `otlp` (HTTP) on `0.0.0.0:4318` (host `4318`); processors
  `memory_limiter` + `batch`; `health_check` extension.
- Exporters on the `logs` pipeline:
  - `otlp_http/transformer`: `endpoint: http://transformer:8000`,
    `encoding: json`, `compression: none` (appends `/v1/logs`);
  - `otlp_http/loki`: `endpoint: http://loki:3100/otlp`, `encoding: json`,
    `compression: gzip`;
  - `debug` (basic) for local inspection.

### 2.2 transformer
- Python 3.11 + FastAPI; `transformer/Dockerfile`, non-root `appuser`,
  healthcheck on `/healthz`; container port `8000`, host `8002:8000`.
- Endpoints: `GET /`, `GET /healthz`, `POST /v1/logs` (OTLP/HTTP JSON only; 415
  for protobuf, 400 for malformed), `GET /metrics`.
- `configs/pricing.json` mounted at `/app/pricing.json` (`PRICING_FILE`).
- Source of truth files: `transformer/app.py` (metrics + wiring),
  `transformer/pricing.py` (pure pricing logic).

### 2.3 prometheus
- Image `prom/prometheus:latest`; `configs/prometheus.yml` — job `transformer`,
  scrape interval 10s, target `transformer:8000`, `rule_files` →
  `configs/prometheus-alerts.yml`. Host `9090`. Volume `prometheus_data`.
- Alert rules (local only): `DSHHighSessionCost`, `DSHUnknownModelGrowing`,
  `DSHLowCacheHitRate`.

### 2.4 loki
- Image `grafana/loki:latest`; `configs/loki.yaml` (single binary, filesystem
  storage, 168h retention, `allow_structured_metadata: true`); OTLP at
  `:3100/otlp`; host `3100`; volume `loki_data`.
- Indexing note: only `service_name` becomes a stream label; `session.id`,
  `model`, `tool`, `event.seq`, `level` arrive as **structured metadata**
  (query with `| field=~"..."`, not `{field="..."}`).

### 2.5 grafana
- Image `grafana/grafana:latest`; admin/admin; provisioning mounts read-only.
- Data sources: `prometheus` (uid `prometheus`, default) and `loki` (uid `loki`).
- Dashboards (auto-imported via `dashboards.yaml`): `dsh-token-usage`,
  `dsh-efficiency`, `dsh-effectiveness`, `dsh-session-logs`.

## 3. Metrics and labels

| Metric | Type | Labels |
|--------|------|--------|
| `dsh_tokens_total` | counter | type, session_id, model, tool |
| `dsh_log_records_total`, `dsh_log_records_dropped_total` | counter | — |
| `dsh_cost_usd_total` | counter | type, session_id, model, tool, effort, period, price_version |
| `dsh_cache_savings_usd_total` | counter | session_id, model, tool, effort, period, price_version |
| `dsh_cost_unknown_model_total` | counter | — |
| `dsh_session_outcome_total` | counter | status, session_id, model, tool, effort |
| `dsh_result_files_changed_total`, `dsh_result_lines_added_total`, `dsh_result_lines_deleted_total`, `dsh_result_methods_added_total` | counter | session_id, model, tool, effort |
| `dsh_result_quality` | gauge | session_id, model, tool, effort |

Token-type → billing slot: `uncachedInputTokens→input_miss`,
`cacheReadTokens→input_hit`, `cacheWriteTokens→input_write`,
`outputTokens→output`. `decodeTokens` is not billed.

### 3.1 Pricing rules (`pricing.py`, `pricing.json`)
- `route_map`: `deepseek-chat→deepseek-v4-flash`, `deepseek-reasoner→deepseek-v4-pro`.
- `default_effort`: `low`; override from leaf `effort`/`reasoningEffort`.
- Peak windows `[[1,4],[6,10]]` UTC, Mon–Fri; off-peak otherwise. Chosen by
  **record timestamp** (UTC).
- Price row active when `effective_from <= ts`; most recent wins; no row → unbilled
  (cost 0) and unknown-route records bump `dsh_cost_unknown_model_total`.
- Result terminal events (leaf `status`) are excluded from token/cost counting.

## 4. Result contract (session terminal event)

JSON in the terminal record's body/attributes (leaf-matched): `status`
(completed/failed/error, required), optional `quality_estimate` (0..1),
`files_changed`, `lines_added`, `lines_deleted`, `methods_added`. Success =
`status == completed`. Main efficiency metric = cost per successful session.

## 5. Ports

| Host | Container | Service | Purpose |
|------|-----------|---------|---------|
| 4318 | 4318 | otel-collector | OTLP/HTTP logs receiver |
| 8002 | 8000 | transformer | `/metrics` / log intake |
| 9090 | 9090 | prometheus | PromQL / rules UI |
| 3100 | 3100 | loki | OTLP log ingest + query |
| 3000 | 3000 | grafana | Dashboards |

## 6. Security considerations
- Traffic stays on the host; nothing is exposed beyond the listed host ports.
- Transformer keeps only in-memory state (metrics + bounded dedup LRU).
- DSH `FULL` mode exports raw content — keep it pointed at this local collector.
