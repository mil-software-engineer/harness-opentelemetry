# Development Guide for the Telemetry Export Project

## 1. Project Setup for Development

### 1.1 Local environment
- Python 3.11+ (Use pyvenv or conda)
- Docker & Docker Compose
- Git

### 1.2 Clone and build
```bash
git clone <your-repo>
#cd into the directory
python -m venv venv
 source venv/bin/activate
 pip install -r requirements.txt
	# (for testing)
pip install flake8 black isort mypy
pip install pytest
```

## 2. Code Structure
The transformer is a single FastAPI application containing two endpoints:

- `POST /v1/logs`: Accepts log records in OTLP HTTP format.
- `GET /metrics`: Exposes Prometheus metrics.

The business logic is contained in function `extract_tokens(and_labels()`, which parses a record and yields a list of (type, value, labels) tuples.

## 3. Coding Standards

### 3.1 Python formatting
Use PEP 8 style. You can run `flake8`, hsort, and black to format code.

### 3.2 Static typing
We use mypy for static typing. All functions should have type hints.

### 3.3 Testing
Tests are written using pytest. They should cover:
- Parsing of valid and invalid log records.
- Ensuring correct label assignment.
- Checking that certain fields don't cause crashes.

## 4. Building the Docker Image
To build the transformer image locally:
```bash
docker build -t transformer ./transformer
```

Or use Docker Compose to rebuild all services:
```bash
docker-compose build
```

## 5. Testing Locally
You can test the transformer in isolation using uvicorn:

```bash
uvicorn app:app --host 0.0.0.0 --port 4319
```

Then send a sample OTLP log payload:
```bash
curl -X POST http://localhost:4319/v1/logs -R -H "Content-Type: application/json" -d '{resourceLogs: [{scopeLogs: [{ logRecords: [{ body: {uncachedInputTokens: 10}, attributes: {session.id: "test", model: "depseek", tool: "coding"}}]}]}]}'

```

## 6. Contributing

### 6.1 Branching
- Main branch is `main`.
- Create a feature branch for changes.
- Open a pull request against `main`.

### 6.2 Commit messages
Use conventional commit messages (e.g. `fix: update extraction of token fields`).

### 6.3 Code review
All changes must be reviewed. Ensure:
- Formatting (PEP 8).
- Static typing (mypy) passes.
- Tests pass (pytest).
- No new dependencies without consent.

## 7. Releasing
This project does not have a formal release cycle. To deploy a new version, simply pull container images and restart the stack.

For production, consider tagging the images and using a private registry.
