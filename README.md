# Enterprise LLM Shadow Deployment & A/B Testing Framework

This is a production-grade Shadow Deployment and A/B Testing framework designed specifically for LLMs. It runs **entirely locally, at $0 cost**, using open-source tools.

## Architecture

* **FastAPI:** Async router and API gateway.
* **Ollama:** Local model execution (Control and Challenger models).
* **Redis:** Background queueing for non-blocking execution.
* **SQLite:** Durable evaluation history and audit trails.
* **Prometheus & Grafana:** System telemetry, cost estimations, and latency tracking.

## Getting Started

1. **Install Requirements**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configuration**
   Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
   *Fill in your local values, especially `ADMIN_API_KEY` and `EXPERIMENT_SALT`.*

3. **Infrastructure (Docker)**
   Start the observability stack and queues:
   ```bash
   cd deployments
   docker compose up -d
   cd ..
   ```

4. **Start the API**
   ```bash
   fastapi dev src/main.py
   ```

5. **Start the Evaluation Worker**
   *(Run this in a separate terminal)*
   ```bash
   python -m src.evaluation.evaluator
   ```

## Key Features

* **Zero-Latency Shadow Mode**: The challenger model executes entirely in the background via Redis queues, ensuring the client receives the control response instantly.
* **Deterministic A/B Routing**: Uses SHA-256 hashing to guarantee that the same user always hits the same variant bucket.
* **Hot-reloadable Kill Switch**: You can flip the `shadow_enabled_global` flag via the `/admin/config` endpoint without restarting the application.
* **Resilience**: Features automatic timeouts, retries with exponential backoff, and circuit breakers that trip if a local model goes down.
* **Dashboards**: Pre-provisioned Grafana dashboards automatically visualize model latencies, simulated token costs, and circuit breaker health.

## Testing

Run the local quality gate:
```bash
pytest tests/
```
