# Requirements Specification (SRS) — DSH Telemetry Export

## 1. Introduction
Functional and non-functional requirements for capturing DeepSeek Harness (DSH)
session telemetry locally, converting it into Prometheus token/cost metrics,
storing raw logs in Loki, and presenting results in Grafana.

## 2. Objective
Let developers/administrators monitor DSH token usage, **USD cost**, cache
effectiveness and session **outcomes/results** per session, model, tool and
effort in real time, using a local, self-contained stack (no external SaaS).

## 3. Scope
- Receive OTLP/HTTP logs from DSH (log records only — DSH does not emit metric
  series).
- Transform logs into metrics: tokens (`dsh_tokens_total`), edge-computed USD
  cost (`dsh_cost_usd_total`, `dsh_cache_savings_usd_total`,
  `dsh_cost_unknown_model_total`) and session results
  (`dsh_session_outcome_total`, `dsh_result_*_total`, `dsh_result_quality`).
- Store the raw log stream in Loki for session context.
- Scrape metrics into Prometheus, evaluate alert rules, and render dashboards in
  Grafana.
- Run entirely locally.

## 4. Functional requirements

### 4.1 Data capture
- Receive OTLP/HTTP log records on host port `4318` (collector).
- Support DSH `FULL` and `FEEDBACK_ONLY` modes (default `DISABLED`).
- Handle token fields `uncachedInputTokens`, `cacheReadTokens`,
  `cacheWriteTokens`, `outputTokens`, `decodeTokens`, flat or nested.
- Recognise session **terminal events** by a `status` leaf
  (completed/failed/error) with optional `quality_estimate` and work fields.
- De-duplicate deliveries on `(session.id, event.seq)` (bounded LRU) so counting
  stays idempotent under collector retries.

### 4.2 Data transformation
- Emit `dsh_tokens_total` (labels type, session_id, model, tool).
- Compute **USD cost at the edge**: model route→tariff normalisation, effort
  tag, peak/off-peak by record timestamp, versioned `effective_from` pricing
  rows. `decodeTokens` is counted but not billed.
- Terminal events produce outcome/work counters and a quality gauge, never
  token/cost series.

### 4.3 Metric exposure & log storage
- Prometheus scrapes `transformer:8000/metrics` on a 10s interval.
- Collector re-exports the same stream to Loki (`:3100/otlp`), 168h retention.
- Transformer exposes `GET /healthz` and counters of received/dropped records.

### 4.4 Visualization & alerting
- Grafana pre-provisioned with **Prometheus** and **Loki** data sources.
- Four auto-imported dashboards: **DSH Token Usage**, **DSH Efficiency**,
  **DSH Effectiveness**, **DSH Session Logs**.
- Prometheus evaluates alert rules locally (no external notifier).

### 4.5 Configuration and operation
- DSH telemetry controlled by env vars (see §6).
- All components containerised via Docker Compose; volumes back Prometheus,
  Grafana and Loki data; services restart automatically; transformer health-checked.

## 5. Non-functional requirements

### 5.1 Performance
- ≥ 100 log records/sec without significant latency; documents < 200 ms on
  average.

### 5.2 Security & privacy
- No remote endpoints; all traffic confined to the host. Redaction/PII policy is
  out of scope (local + open-source repos).

### 5.3 Reliability
- Idempotent counting via de-duplication. `restart: unless-stopped`; transformer
  has a `HEALTHCHECK`. Transformer state is in-memory (reset on restart).

### 5.4 Maintainability
- Python code simple, typed, PEP 8; logic split into pure (`pricing.py`) and
  wiring (`app.py`); config via YAML/JSON + env.

## 6. Environment variables

### 6.1 DSH (set before starting/restarting DSH)
| Variable | Description | Default |
|----------|-------------|---------|
| `DSH_TELEMETRY_MODE` | `FULL` \| `FEEDBACK_ONLY` \| `DISABLED` | `DISABLED` |
| `DSH_TELEMETRY_OTLP_URL` | OTLP/HTTP log push URL, e.g. `http://localhost:4318/v1/logs` | — |
| `DSH_TELEMETRY_DISABLED` | Any non-empty value disables telemetry | — |

### 6.2 Transformer (Compose/container)
| Variable | Description | Default |
|----------|-------------|---------|
| `PRICING_FILE` | Path to the pricing table JSON | `/app/pricing.json` |
| `DSH_DEDUP_CAPACITY` | De-duplication LRU capacity | `100000` |

## 7. Acceptance criteria
1. Token/cost/result metrics appear in Prometheus within ~10s of a forwarded
   record.
2. Raw logs appear in Loki and are searchable by session in Grafana.
3. All containers start without errors; the transformer reports healthy.

## 8. Assumptions and dependencies
- DSH runs with the `@deepseek-ai/dsh-session-telemetry-otel` module.
- Docker Engine 20.10+ and Docker Compose v2.
- Free host ports `4318`, `8002`, `9090`, `3100`, `3000`.
- The user can export env vars for DSH.
