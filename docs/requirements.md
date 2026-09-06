# Requirements Specification (SRS) for DSH Telemetry Export

## 1. Introduction
This document describes the functional and non-functional requirements for the system that captures DeepSeek Harness (DSH) session telemetry, converts it into Prometheus-friendly metrics, and presents it in Grafana dashboards.

## 2. Project Objective
To enable developers and system administrators to monitor token usage and costs of DSH sessions in real time, by deploying a local, self-contained observability stack.

## 3. Scope
The scope is limited to:
- Receive OTLP/HTTP logs from DSH (only log records, not metrics).
- Transform these logs into Prometheus metric series counting tokens by type, session, model, tool, and day.
- Expose these metrics to Prometheus for scraping.
- Provide a Grafana dashboard with pre-defined panels for visualization.
- Ensure all components run locally with no dependency on external SaaS.

## 4. Functional Requirements

### 4.1 Data Capture
- The system shall receive OTLP/HTTP log records from DSH on port 4318.
- It shall support both FULL and FEEDBACK_ONLY modes (though FULL is preferred for complete analysis).
- The system must handle the following token fields: `uncachedInputTokens`, cacheReadTokens`, cacheWriteTokens`, outputTokens`, decodeTokens`.
- Deduplication shall be performed based on `session.id + event.seq` (optional).

### 4.2 Data Transformation
- The system shall extract token counts from the log records body or attributes.
- It shall create a Prometheus counter metric `dsh_tokens_total` with labels: `type`,  session_id`, `model`, `tool`, `day`.
- The transformation must be performed by a separate component (transformer) because the Otel Collector cannot directly convert logs to metrics.

### 4.3 Metric Exposure
- Prometheus shall scrape the metric from the transformer on `transformer:8000`.
- The scrape interval shall be 10 seconds or configurable.

### 4.4 Visualization
- Grafana shall be pre-configured with a Prometheus data source.
- A dashboard shall be provided with at least three panels:
   - Total tokens over time (graph),
   - Tokens by session (table),
   - Daily tokens by model (graph).

### 4.5 Configuration and Operation
- The system shall be controlled via environment variables for DSH (see Section 6).
- All components shall be containerized using Docker Compose.
- Persistent volumes shall be used for Prometheus and Grafana data.

### 4.6 Monitoring and Logging
- Each component shall output logs to stdout/container logs.
- The transformer shall log errors and acknowledge of received records (at debug level).
- Metrics shall be exposed for monitoring by Prometheus.

## 5. Non-Functional Requirements

### 5.1 Performance
- The system shall handle a minimum of 100 log records per second without significant latency.
- The transformer must process records within 200 ms on average.

### 5.2 Security
- No remote endpoints are used; all traffic is confined to localhost.
- No tokens or secrets are hard-coded in the code.
- The transformer does not store or persist any data except in-memory counters.

### 5.3 Reliability
- Tasks should be idempotent: if the transformer fails, metrics will be unavailable, but this should not affect!OH operation.
- Containers should restart automatically (using `restart: unless` policy).

### 5.4 Maintainability
- Code should be simple, well-commented, and follow PEP 8 style for Python.
- Configuration is done via environment variables or clear YAML files.

## 6. Environment Variables for DSH Telemetry
The following env variables must be supported by the system:

    | Env Variable               | Description                                                          | Default           |
    |------------------------------|--------------------------------------------------------------------|-----------------|
    | DSH_TELEMETRY_MODE        | Either FULL, FEEDBACK_ONLY, or DISABLED                          | DESAEBLE         |
    | DSH_TELEMETRY_OTLP_URL    | The HTTP target for OTLP/HTTP log push (e.g. `http://localhost:4318/v1/logs`)        | (required in FULL/FEEDBACK_ONLY) |
    | DSH_TELEMETRY_DISABLED    | Any non-empty value disables telemetry entirely           | (optional)          |

## 7. Acceptance Criteria
The system is considered acceptable if:
1. Metrics appear in Prometheus within 10 seconds of first record.
2. Grafana dashboard displays token data for active sessions.
3. All containers start without errors and remain healthy (health checks).

## 8. Assumptions and Dependencies
-  DSH is already running with thd `dsh-session-telemetry-otel` module enabled.
-  The local machine has Docker and Docker Compose installed.
-  Ports 4318, 4319, 8000, 9090, 3000 are available.
-  The user has permission to expose env variables for DSH.
