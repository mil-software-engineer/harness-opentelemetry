# Operations Guide — DSH Telemetry Export

## 1. Quick reference

| Operation               | Command                                                          |
|-------------------------|------------------------------------------------------------------|
| Start the stack         | `docker compose up -d --build`                                   |
| Stop the stack          | `docker compose down` (add `-v` to drop volumes)                 |
| Restart a service       | `docker compose restart <service>`                               |
| View logs               | `docker compose logs -f <service>`                               |
| Container status        | `docker compose ps`                                              |
| Transformer metrics     | `curl http://localhost:8002/metrics`                             |
| Prometheus UI           | `http://localhost:9090`                                          |
| Loki query              | `curl -G http://localhost:3100/loki/api/v1/query_range --data-urlencode 'query={service_name="deepseek-harness"}'` |
| Grafana UI              | `http://localhost:3000` (admin/admin)                            |
| Re-provision Grafana    | `docker compose restart grafana` (reads datasource/dashboard files) |
| Reset data              | `docker compose down -v`                                         |

Host ports required: `4318`, `8002`, `9090`, `3100`, `3000`.

## 2. Deployment

```bash
git clone <your-repo> && cd <your-repo>
docker compose up -d --build
docker compose ps   # otel-collector, transformer(healthy), prometheus, loki, grafana
```

## 3. Configuring DSH telemetry

Before starting (or restarting) DSH:

```bash
export DSH_TELEMETRY_MODE=FULL
export DSH_TELEMETRY_OTLP_URL=http://localhost:4318/v1/logs
# start / restart DeepSeek Harness
```

- `FULL` = live records; `FEEDBACK_ONLY` = replay on `feedback/record`.
- Disable: `DSH_TELEMETRY_MODE=DISABLED` or `DSH_TELEMETRY_DISABLED=1`.
- `FULL` exports raw content — keep the URL on this local collector.

## 4. Verification

1. `docker compose ps` — all services `Up`; transformer `(healthy)`.
2. `curl http://localhost:8002/metrics | grep ^dsh_` — token/cost/result series.
3. Prometheus `http://localhost:9090` — target `transformer` `UP`, three rules
   loaded (`/api/v1/rules`).
4. Loki — the same OTLP record is queryable:
   `curl -G http://localhost:3100/loki/api/v1/query_range --data-urlencode 'query={service_name="deepseek-harness"}'`.
5. Grafana `http://localhost:3000` — dashboards `DSH Token Usage`, `DSH
   Efficiency`, `DSH Effectiveness`, `DSH Session Logs`; Prometheus and Loki
   sources connected.

## 5. Troubleshooting

### 5.1 No metrics
- Confirm DSH is sending (log level debug) and env vars are set.
- `docker compose logs otel-collector` — starts clean, `debug` exporter prints
  `log records: N`.
- `docker compose logs transformer` — prints `ingested OTLP logs document: N`.
- `curl http://localhost:8002/metrics` exposes `dsh_*`.
- Prometheus target `transformer:8000` must be `UP`.

### 5.2 No logs in Loki / session search empty
- Collector must have `otlp_http/loki` in the `logs` pipeline exporters.
- Loki indexes only `service_name` as a stream label; `session.id` is structured
  metadata — query `{service_name="deepseek-harness"} | session_id=~"..."`, not
  `{session_id="..."}`.
- `docker compose logs loki` for ingest errors; check `:3100` reachable.

### 5.3 Cost shows 0 or unknown-route growth
- `dsh_cost_unknown_model_total` growing → telemetry route is not in
  `route_map` of `configs/pricing.json`; update the map (and bump pricing if
  prices changed).
- Prices come from `configs/pricing.json` (`effective_from` rows); keep it
  versioned and mounted (`PRICING_FILE=/app/pricing.json`).

### 5.4 Container crashes / permission errors
- Config bind mounts are `:ro`; if a service reports `permission denied`, ensure
  host files under `configs/` are world-readable
  (`chmod -R a+rX configs`), then `docker compose restart`.
- Inspect: `docker compose logs <service>`.

### 5.5 Reset
- `docker compose down -v && docker compose up -d`.

## 6. Monitoring and maintenance
- Watch cost/cache dashboards for spend and reuse; `DSHUnknownModelGrowing`
  signals unmapped routes; `DSHLowCacheHitRate` signals prompt prefixes not
  being reused.
- Rebuild after changing `transformer/` or configs: `docker compose up -d --build`.
- Prometheus/Grafana/Loki state is on named volumes; back them up if history
  matters.
