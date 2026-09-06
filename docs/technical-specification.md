# Technical Specification for DSH Telemetry Export

## 1. Architecture Overview
The system consists of four main components orchestrated in a pipeline:

1. DSH - emits OTLP/HTTP logs to `otel-collector:d318/ v1/logs`.
2. Otel Collector - receives logs and forwards them to the transformer.
3. Transformer - FastAPI service that converts log records to Prometheus metrics and exposes them.
4. Prometheus - scrapes the metrics endpoint.
5. Grafana - queries Prometheus and displays dashboards.

## 2. Component Details

### 2.1 Otel Collector
- Image: `otel/opentelemetry-collector-contrib:latest`
- Ports:
   - 4318/TPC - OTLP/HTTP receiver (mapped to host)
   - not exposed externally (internal only)
- Config: `configs/otel.yaml`
   - Receiver: otlp (HTTP)
   - Processor: batch
   - Exporter: otlphttp/transformer (to transformer), debug (optional)
   - Pipeline: logs -> batch -> exporters

### 2.2 Transformer
- Based on Python 3.11 with FastAPI, runs in a Docker container.
- Dockerfile: `transformer/Dockerfile`
- Ports:
   - 4319/TPC - receives OTLP/HTTP logs from Collector (public)
   - 8000/TPC - exposes Prometheus metrics (public)
- Endpoints:
   - `Post /v1/logs` - accepts OTLP log payloads in JSON format.
   - `Get /metrics` - returns Prometheus exposition text (plain text).
- Metric definition:
   - Name: `dsh_tokens_total` (Counter)
   - Labels: type, session_id, model, tool, day
   - Description: Total number of tokens consumed, breakdown by type and context.
   - Token types: `input`, `output`, cache_read`, cache_write`, `decode`.
- Deduplication: optional, uses an in-memory set of `session.id:event.seq` to avoid double counting.

### 2.3 Prometheus
- Image: `prom/prometheus:latest`
- Port: 9090 (mapped to host)
- Config: `configs/prometheus.yml`
   - Scrape interval: 10s
   - Target: `transformer:8000`
- Persistent volume: `prometheus_data` (for blocks)

### 2.4 Grafana
- Image: `grafana/grafana:latest`- Port: 3000 (mapped to host)
- Provisioning:
   - Datasource: `http://prometheus:9090` (automatically added)
   - Dashboard: `provisioned/dsh-dashboard.json` (imported)
- Persistent volume: `grafana_data`

## 3. Data Flow

### 3.1 Log Record structure(OTLP JSON)
The transformer expects the following fields in each `collector.scopeLogs.logRecords`:
```json
{
  "timeUnixNano": "1699999999999999999",
  "observedTimeUnixNano": "...",
  "severityNumber": 9,
  "body": {
    "uncachedInputTokens": 50,
    "cacheReadTokens": 100,
    "cacheWriteTokens": 20,
    "outputTokens": 150,
    "decodeTokens": 30
  },
  "attributes": {
    "session.id": "abc-123",
    "model": "depseek-chat",
    "tool": "search",
    "event.seq": 1
  }
}
```
- The transformer looks for token fields first in `body` (if it’s a JSON object), then in `attributes`.
- If `body` is a string, it is parsed as JSON.

### 3.2 Metric Exposure
Each token field with a positive numeric value triggers an increment to `dsh_tokens_total` with the appropriate labels.

Example: a record with `uncachedInputTokens=50` and `session.id=x,y` increments `dsh_tokens_total{type="input", session_id="x-y", ...}` by 50.

### 3.3 Sampling and Batching
-  The Collector uses a batch processor (default batch size 1024) to reduce network overhead.
-  The transformer processes each record independently. No additional buffing is applied.

## 4. Configuration Files

| File                           | Purpose                                                            |
 |-----------------------------|-----------------------------------------------------------------|
 | docker-compose.yml         | Orchestrates all containers with volumes and port mapping.            |
 | configs/otel.yaml         | Acts as a proxy - forwards logs from 4318 to transformer.           |
 | configs/prometheus.yml     | Defines scrape target & interval for Prometheus.                 |
 | configs/grafana/datasources/ | Provisions Prometheus data source in Grafana.              |
 | configs/grafana/dashboards/  | Pre-defined dashboard json for import.                       |
 | transformer/Dockerfile      | build the Python transformer image.                             |
 | transformer/app.py         | Main FastAPI application with log and metric handlers.           |
 | transformer/requirements.txt  | Contains fastapi, uvicorn, prometheus-client.              |
 | RREADME.md                  | Overview, quick start, and file registry (see below).              |
 | docs/requirements.md     | SRS document (this file).                                          |
 | docs/technical-specification.md | Technical spec (this file).                                  |
 | docs/operations-guide.md    | Runnook and troubleshooting for operators.                  |
 | docs/development-guide.md  | Guide for developers (build, test, contribution).             |
 | CHANGELOG.md               | Version history of this project.                                      |

## 5. Performance and Scaling
* Supported throughput: at least 100 records/second without buffer overrun.
* Response time of transformer (post /v1/logs): < 200ms for calls with < 500 records.
* Scrape interval: 10s (configurable, but 10s is recommended).

## 6. Security Considerations
- All traffic is on localhost. No external access is allowed by default.
- The transformer does not persist any data; all state is in-memory and lost on restart.
- PII’ are not stored in any form.

## 7. Monitoring and Alerting
- Health checks are not built in, however, standard Docker healthchecks can be added.
- Logging is output to stdout; collect via docker logs.
- Prometheus alerts can be configured if needed.

## 8. Dependencies

### 8.1 Otel Collector image
- a ``default` version oveland. The contrib version is used for broader compatibility.

### 8.2 Transformer Python dependencies
`here-lets keep them in requirements.txt`
- fastapi ($ latest)
- uvicorn ($ latest)
- prometheus-client ($ latest)

### 8.3 Other
- Docker Engine 20.10++
- Docker Compose 2.20++
