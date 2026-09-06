# Operations Guide for the DSH Telemetry Export

## 1. Quick Reference

| Operation                                 | Command                                                                |
 |----------------------------------------|------------------------------------------------------------------------------|
 | Start all services                  | `docker-compose up -d`                                                              |
 | Stop all services                  | `docker-compose down` , `docker-compose down -v` (volumes)          |
 | Restart a single service            | `docker-compose restart <service-name>`                                   |
 | View logs of a service            | `docker logs <service-name>`                                                |
 | Check transformer metrics           | `curl http://localhost:8000/metrics`                                            |
 | Check Otel Collector health       | `curl http://localhost:13134/v1/health` (TNL) or curl :4318/v1/health` (HTTP) |
 | Check Prometheus targets           | `curl http://localhost:9090/targets`                                              |
 | Access Grafana UI                 | Open `http://localhost:3000` (login: admin/admin)                        |
 | Reload Grafana datasource         | Restart grafana: `docker-compose restart grafana`                        |
 | Reset persistent volumes            | `docker-compose down -v ; docker-volume rm -f prometheus_data grafana_data`        |

## 2. Deployment Steps (step-by-step)

1. Clone this repository into your local machine.
   ``gbash
   git clone https://github.com/your-repo/telemetry-export.git
   cd telemetry-export
   ```

2. Ensure Docker and Docker Compose are installed.
3. Start the stack:
   ``bash
   docker-compose up -d
   ```
4. Verify that all containers are running:
   ``bash
   docker-compose ps
   ```
5. Configure DSH to send telemetry to the collector by setting environment variables (see below).
6. Restart DSH so that the new variables are picked up.
7. Check that metrics appear in Prometheus by opening `http://localhost:9090`.
8. In Grafana, add the Prometheus data source (it should be automatically provisioned).
9. Import the dashboard from `configs/grafana/dashboards/dsh-dashboard.json` or create a new one.

## 3. Configuring DSH Telemetry
To enable telemetry from DSH, set the following environment variables before starting DSH (or restart):

```bash
export DSH_TELEMETRY_MODE=FULL
export DSH_TELEMETRY_OTLP_URL=http://localhost:4318/v1/logs
```

To disable telemetry temporarily:
```bash
export DSH_TELEMETRY_MODE=DISABLED
```

Or use `DSH_TELEMETRY_DISABLED=1` to force disable.

## 4. Troubleshooting

### 4.1 No metrics appearing
* Check DSH logs to confirm it is sending records (set log level to debug).
* View Otel Collector logs: `docker logs otel-collector` - look for errors or connection refusals.
* Check transformer logs: `docker logs transformer` - it should print a received message for each batch.
* Verify that the transformer is exposing metrics: `curl localhost:8000/metrics` should return `mypc_dsh_tokens_total`.
* Ensure Prometheus is scraping the correct target: `curl localhost:9090/targets` should list `transformer:8000`.

### 4.2 Dashboard not populating
* Check Grafana data source url is `prometheus:9090` (not `localhost`).
* Verify that the datasource is successfully connected (in Grafana, DataSources > Prometheus > Test).
* Refresh the dashboard or import it again.

### 4.3 Container crashes or restarts
* Increase memory limits in `docker-compose.yml` for the transformer if needed.
* Use `docker-compose logs <service>` to inspect errors.
* If the transformer fails, restart it: `docker-compose restart transformer`.

### 4.4 Persistent data issues
*If you want to reset all data, run `docker-compose down -v` and then remove the volumes:
```bash
docker volume rm -f prometheus_data grafana_data
```

## 5. Monitoring and Maintenance

- Regularly check the size of prometheus blocks (volume). Clean if too large.
- Update DSH or the transformer image by rebuilding the containers (`docker-compose build` or use new images).
- Consider using a reverse proxy (e.g. nginx) if you need to expose the dashboard publicly (not recommended).
