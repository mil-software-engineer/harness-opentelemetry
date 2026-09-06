# Operations Guide — DSH Telemetry Export

## 1. Quick reference

| Operation               | Command                                                            |
|-------------------------|--------------------------------------------------------------------|
| Start the stack         | `docker compose up -d --build`                                     |
| Stop the stack          | `docker compose down` (add `-v` to drop volumes)                   |
| Restart a service       | `docker compose restart <service>`                                 |
| View logs               | `docker compose logs -f <service>`                                 |
| Container status        | `docker compose ps`                                                |
| Transformer metrics     | `curl http://localhost:8002/metrics`                               |
| Prometheus UI           | `http://localhost:9090`                                            |
| Grafana UI              | `http://localhost:3000` (admin/admin)                              |
| Re-provision Grafana    | `docker compose restart grafana` (reads datasource/dashboard files)|
| Reset persistent data   | `docker compose down -v`                                           |

## 2. Deployment

```bash
git clone <your-repo> && cd <your-repo>
docker compose up -d --build
docker compose ps   # all four services Up; transformer Healthy
```

Host ports required: `4318`, `8002`, `9090`, `3000`.

## 3. Configuring DSH telemetry

Before starting (or restarting) DSH:

```bash
export DSH_TELEMETRY_MODE=FULL
export DSH_TELEMETRY_OTLP_URL=http://localhost:4318/v1/logs
# start / restart DeepSeek Harness so the env is picked up
```

- `FULL` = live records; `FEEDBACK_ONLY` = replay/redact on `feedback/record`.
- Disable temporarily: `DSH_TELEMETRY_MODE=DISABLED` or set
  `DSH_TELEMETRY_DISABLED=1`.

> `FULL` exports raw record content — keep the URL pointed at this local
> collector (`localhost:4318`), never an untrusted remote endpoint.

## 4. Verification

1. `docker compose ps` — all services `Up`; transformer `(healthy)`.
2. `curl http://localhost:8002/metrics | grep ^dsh_tokens_total` — a series
   appears once records arrive.
3. `http://localhost:9090` → query `dsh_tokens_total`; the `transformer` target
   should be `UP`.
4. `http://localhost:3000` → **DSH Token Usage** dashboard is already imported
   and the Prometheus data source is connected.

## 5. Troubleshooting

### 5.1 No metrics appearing
- Confirm DSH is sending (its log level = debug) and env vars are set.
- `docker compose logs otel-collector` — the collector must start without config
  errors and show `debug` exporting (`log records: N`).
- `docker compose logs transformer` — should print an `ingested OTLP logs
  document: N observation(s)` line per batch.
- `curl http://localhost:8002/metrics` should expose `dsh_tokens_total`.
- `http://localhost:9090/targets` should list job `transformer` → target
  `transformer:8000` as `UP`.

### 5.2 Dashboard empty
- Grafana data source URL must be `http://prometheus:9090` (service name, not
  `localhost`); test it under Data sources → Prometheus → Save & test.
- Dashboard values come from `dsh_tokens_total`; if only a single burst was ever
  sent, rate-based panels can look flat — send records continuously.

### 5.3 Container crashes / permission errors
- Config bind mounts are `:ro`; if a service reports `permission denied`, ensure
  the host files under `configs/` are world-readable (`chmod -R a+rX configs`),
  then `docker compose restart`.
- Inspect: `docker compose logs <service>`; restart: `docker compose restart
  <service>`.

### 5.4 Reset
- Reset metrics/dashboards: `docker compose down -v && docker compose up -d`.

## 6. Monitoring and maintenance

- Watch `dsh_tokens_total` for spend; watch `dsh_log_records_dropped_total` (a
  positive value means records were skipped as duplicates).
- Rebuild after changing `transformer/` or configs: `docker compose up -d
  --build`.
- Prometheus/Grafana state is on named volumes; back them up if you need the
  history.
