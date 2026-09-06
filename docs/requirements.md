# Requirements Specification (SRS) — DSH Telemetry Export

## 1. Introduction

Functional and non-functional requirements for the system that captures
DeepSeek Harness (DSH) session telemetry, converts it into Prometheus-friendly
token metrics, and presents it in Grafana.

## 2. Objective

Let developers/administrators monitor DSH token usage and costs per session,
per model and per tool in real time, using a local, self-contained observability
stack (no external SaaS).

## 3. Scope

- Receive OTLP/HTTP logs from DSH (log records only — DSH does not emit metric
  series).
- Transform logs into the Prometheus counter `dsh_tokens_total`, sliced by
  token type, session, model and tool, and over time (day bucketing is done in
  queries, not as a label).
- Expose the metrics for Prometheus scraping and render them in Grafana.
- Run entirely locally.

## 4. Functional requirements

### 4.1 Data capture

- Receive OTLP/HTTP log records on host port `4318` (collector).
- Support DSH `FULL` and `FEEDBACK_ONLY` modes (default `DISABLED`).
- Handle token fields `uncachedInputTokens`, `cacheReadTokens`,
  `cacheWriteTokens`, `outputTokens`, `decodeTokens`, whether they appear flat
  or nested in the record body/attributes.
- De-duplicate deliveries on `(session.id, event.seq)` with a bounded store to
  keep counting idempotent under collector retries.

### 4.2 Data transformation

- Extract positive numeric token counts from record body or attributes.
- Maintain the Prometheus counter `dsh_tokens_total` with labels `type`,
  `session_id`, `model`, `tool`.
- Transformation is performed by the **transformer** component because the OTel
  Collector cannot convert logs to metrics directly.

### 4.3 Metric exposure

- Prometheus scrapes `transformer:8000/metrics` on a 10s interval.
- The transformer also exposes `GET /healthz` for the container healthcheck and
  counters of received/dropped records for observability.

### 4.4 Visualization

- Grafana is pre-provisioned with a Prometheus data source.
- A dashboard (**DSH Token Usage**) is auto-imported and shows: token throughput
  by type and by model, daily token consumption by type, tokens by session
  (table) and token throughput by tool.

### 4.5 Configuration and operation

- DSH telemetry is controlled by environment variables (see §6).
- All components are containerised via Docker Compose.
- Persistent volumes back Prometheus and Grafana data.
- Containers restart automatically and the transformer is health-checked.

### 4.6 Monitoring and logging

- Each component logs to stdout (`docker compose logs`).
- The transformer logs an ingest line per document and exposes its own metrics.

## 5. Non-functional requirements

### 5.1 Performance
- Handle ≥ 100 log records/sec without significant latency.
- Process documents well under 200 ms on average.

### 5.2 Security
- No remote endpoints; traffic is confined to the host.
- No tokens/secrets are hard-coded; DSH provider keys never appear in session
  events.
- The transformer keeps state only in memory (counters + bounded de-dup LRU);
  nothing is persisted, so nothing is lost beyond metrics on restart.

### 5.3 Reliability
- Counting is idempotent via de-duplication (replay does not double count).
- Containers restart automatically (`restart: unless-stopped`); transformer has
  a `HEALTHCHECK`.

### 5.4 Maintainability
- Python code is simple, typed, commented, PEP 8.
- Configuration lives in YAML/Compose and env vars.

## 6. Environment variables

### 6.1 DSH (set before starting/restarting DSH)

| Variable                  | Description                                                        | Default    |
|---------------------------|--------------------------------------------------------------------|------------|
| `DSH_TELEMETRY_MODE`      | `FULL` \| `FEEDBACK_ONLY` \| `DISABLED`                            | `DISABLED` |
| `DSH_TELEMETRY_OTLP_URL`  | OTLP/HTTP log push URL, e.g. `http://localhost:4318/v1/logs`       | —          |
| `DSH_TELEMETRY_DISABLED`  | Any non-empty value disables telemetry                             | —          |

### 6.2 Transformer (Compose/container)

| Variable             | Description                      | Default  |
|----------------------|----------------------------------|----------|
| `DSH_DEDUP_CAPACITY` | De-duplication LRU capacity      | `100000` |
| `APP_HOST`/`APP_PORT`| Uvicorn bind address/port        | `0.0.0.0`/`8000` |

## 7. Acceptance criteria

1. Metrics appear in Prometheus within ~10s of the first forwarded record.
2. The Grafana dashboard displays token data for active sessions.
3. All containers start without errors; the transformer reports healthy.

## 8. Assumptions and dependencies

- DSH is running with the `@deepseek-ai/dsh-session-telemetry-otel` module.
- Docker Engine 20.10+ and Docker Compose v2.
- Free host ports `4318`, `8002`, `9090`, `3000`.
- The user can export env vars for DSH.
