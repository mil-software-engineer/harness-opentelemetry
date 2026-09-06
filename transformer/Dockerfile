# DSH token-telemetry transformer image.
#
# A small OTLP/HTTP-logs -> Prometheus-metrics generator (see app.py). Kept on
# a slim Python 3.11 base and run as a non-root user.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

WORKDIR /app

# Install dependencies first so source changes do not invalidate the layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application and drop privileges before running it.
COPY app.py ./
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % __import__('os').environ['APP_PORT'], timeout=3); sys.exit(0)" || exit 1

CMD ["sh", "-c", "exec uvicorn app:app --host ${APP_HOST} --port ${APP_PORT}"]
