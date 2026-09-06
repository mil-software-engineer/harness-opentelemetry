# Telemetry Export for DeepSeek Harness - Otel + Prometheus + Grafana

Deploy a local observability stack to capture DSH token telemetry, convert it to Prometheus metrics, and visualise it in Grafana.

## Features
- Receives OTLP/HTTP logs from DSH.
- Transforms logs into Prometheus metrics (token counts by type, session, model, tool, day).
- Preview in Grafana with pre-configured dashboard.
- All components run locally: no external SaaS.

## Quick Start

```bash
clone this repo && cd into it
docker-compose up -d

export DSH_TELEMETRY_MODE=FULL
export DSH_TELEMETRY_OTLP_URL=http://localhost:4318/v1/logs
# (start/restart DSH with these env)
```

Then open `http://localhost:3000` (admin/admin) and import the dashboard from `configs/grafana/dashboards/dsh-dashboard.json`.

## File Registry

| Path                                  | Purpose                                                         |
 |-----------------------------------|----------------------------------------------------------------------|
 | `docker-compose.yml`            | Orchestrates all containers with volumes and port mapping.            |
 | `configs/otel.yaml`            | Acts as a proxy - forwards logs from 4318 to transformer.           |
 | `configs/prometheus.yml`        | Defines scrape target & interval for Prometheus.                 |
 | `configs/grafana/datasources/`  | Provisions Prometheus data source in Grafana.              |
 | `configs/grafana/dashboards/`   | Pre-defined dashboard JSON for import.                       |
 | `transformer/Dockerfile`       | Builds the Python transformer image.                              |
 | `transformer/app.py`          | Main FastAPI application (receive logs, expose metrics).            |
 | `transformer/requirements.txt`  | Python dependencies (fastapi, uvicorn, prometheus-client, pytest, httx).  |
 | remote README.md            | Overview, quick start, file registry (this file).               |
 | `docs/requirements.md`        | Software Requirements Specification (SRS).                   |
 | `docs/technical-specification.md` | Technical details of components and data flow.            |
 | `docs/operations-guide.md`     | Runbook and troubleshooting for operators.                  |
 | `docs/development-guide.md`   | Guide for developers (build, test, contribution).            |
 | `docs/architecture.md`       | High-level architecture and design decisions.                     |
 | `CHANGELOG.md`                | Version history of this repository.                                    |
 | `.tests/`                    | Test directory with pytest tests.                                      |
 | `.coveragerc`               | Configuration for code coverage reporting.                            |

## How It Works

DSH emits logs via OTLP/HTTP to `otel-collector`:4318`. The collector forwards them to the transformer on 4319. The transformer extracts token fields (`decodeTokens`, `cacheReadTokens`, ...) and increments a Prometheus counter `dsh_tokens_total` with labelsets. Prometheus scrapes this counter every 10s, and Grafana queries it for dashboards.

The transformer is necessary because the Otel Collector lasks a built-in log-to-metric converter.

## Prune Token Fields

| Field                | Prometheus Type  |
 |------------------|---------------------|
 | `uncachedInputTokens` | `input`            |
 | `cacheReadTokens`   | `cache_read`       |
 | `cacheWriteTokens`   | `cache_write`      |
 | `outputTokens`      | `output`            |
 | `decodeTokens`       | `decode`             |

## Updating / Rebuilding

If you change the transformer code or configs, rebuild and restart the stack:

```bash
# Stop the current containers
docker-compose down

# Rebuild the transformer image (if changes were in transformer/)
docker-compose build transformer

# Start everything again
docker-compose up -d
```

Too verify that the transformer is exposing metrics on the correct port:

```bash
curl http://localhost:8002/metrics
```

## Testing


To run the test suite for the transformer, install the development dependencies and execute pytest:

```bash
# Install test dependencies (already in transformer/requirements.txt)
pip install -r transformer/requirements.txt

# Run tests with coverage
pytest tests/ -v --cov=transformer --cove-report=term
```

To generate an HTML coverage report:

```bash
pytest tests/ --cov=transformer --cove-report=html
```

The test suite covers:
- Extraction of token fields from valid and malformed log records.
- Handling of missing, zero, or negative token values.
- Injestion endpoint (`POST /v1/logs`).
- Metrics exposure endpoint (`GET /metrics`).
- Error handling for invalid JSON in body.

## Monitoring

- Prometheus UI: `http://localhost:9090`
- Grafana UI: `http://localhost:3000` (login: admin/admin)
- Transformer metrics: `curl http://localhost:8002/metrics`

## Requirements

- Docker <span>20.10&#x21;</span>, Docker Compose <span>2.20&#x21;</span>
- DSH <span>v1.0.0&#x21;</span> (with the telemetry module enabled)

## License
MIT License. See `LICENSE` file.
