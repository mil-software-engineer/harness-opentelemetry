# Development Guide — DSH Telemetry Export

## 1. Local environment
- Python 3.11+ (image uses `python:3.11-slim`), Docker Engine 20.10+ / Compose v2.

## 2. Project layout
```
docker-compose.yml                        # orchestration (5 services)
configs/
  otel.yaml                               # collector: OTLP → transformer + Loki
  pricing.json                            # versioned pricing table + route_map
  loki.yaml                               # single-binary Loki config
  prometheus.yml / prometheus-alerts.yml  # scrape + alert rules
  grafana/{datasources,dashboards}/       # provisioning + 4 dashboards
transformer/
  Dockerfile, app.py, pricing.py
  requirements.txt, requirements-dev.txt
tests/                                    # test_app.py, test_pricing.py
docs/                                     # architecture, SRS, spec, runbook, dev guide
```

## 3. Key code in `transformer/`
- `app.py` — FastAPI app; `_MetricsStore` registers token/cost/result metrics;
  `consume()` routes each record: terminal event (leaf `status`) → result; else
  → cost + tokens. Endpoints `/`, `/healthz`, `/v1/logs`, `/metrics`.
- `pricing.py` — pure functions: `normalize_model`, `classify_unknown`,
  `effort_for`, `period_for` (peak/off-peak by timestamp), `price_for`,
  `load_pricing`. No Prometheus/IO coupling.
- Wire parsing is in `app.py` (`_iter_leaves`, `_unwrap_any_value`): handles OTLP
  proto-JSON `AnyValue`, `repeated KeyValue` attributes, JSON-string bodies and
  plain maps; fields matched by leaf name.

## 4. Setup
```bash
python3 -m venv .venv
.venv/bin/pip install -r transformer/requirements-dev.txt   # runtime + pytest + httpx
```

## 5. Tests
```bash
.venv/bin/python -m pytest tests/
```
Covers: token/result extraction (flat, nested, proto-JSON), label resolution and
fallbacks, zero/negative handling, de-duplication, pricing (slots, peak/off-peak,
effective_from, model mapping, `decode` not billed), cost/savings example values,
API behaviour (400/415/healthz/metrics), per-session isolation.

## 6. Running standalone
```bash
cd transformer && ../.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
# POST an OTLP/HTTP JSON document to /v1/logs; inspect /metrics
```
`PRICING_FILE` defaults to `/app/pricing.json`; set it when running outside the
container.

## 7. Building / running the stack
```bash
docker compose build transformer      # or: docker compose up -d --build
docker compose up -d
```

## 8. Contributing
- Keep changes scoped: if behaviour (ports/topology/metric labels/pricing)
  changes, update `docker-compose.yml`, `configs/*` and `docs/*` together.
- Add tests in `tests/` mirroring existing patterns; run `python -m pytest`.
- Keep `pricing.py` pure and `app.py` thin; PEP 8, typed.
- One USD source of truth: change cost only through `configs/pricing.json` +
  `pricing.py` logic, never a second parallel engine.
