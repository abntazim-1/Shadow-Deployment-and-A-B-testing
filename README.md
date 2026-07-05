# Enterprise LLM Shadow Deployment & A/B Testing Framework

A production-grade, real-time shadow deployment and A/B testing framework for Large Language Models (LLMs). This framework enables engineering and research teams to safely evaluate challenger LLM APIs against a production control model — with **zero impact to end-user latency** — and make data-driven model promotion decisions backed by rigorous statistical analysis.

---

## Table of Contents

1. [Overview](#overview)
2. [Core Architecture](#core-architecture)
3. [How Shadow Testing Works](#how-shadow-testing-works)
4. [Project Structure](#project-structure)
5. [Component Deep-Dive](#component-deep-dive)
6. [Metrics & Observability](#metrics--observability)
7. [Prerequisites](#prerequisites)
8. [Quick Start](#quick-start)
9. [Configuration Reference](#configuration-reference)
10. [API Reference](#api-reference)
11. [Running the Evaluation Worker](#running-the-evaluation-worker)
12. [Viewing the Grafana Dashboard](#viewing-the-grafana-dashboard)
13. [Statistical Methodology](#statistical-methodology)
14. [Resilience & Safety](#resilience--safety)

---

## Overview

Deploying a new LLM to production carries real risk. A model that scores well on a benchmark may perform poorly under real-world traffic patterns — with higher latency, inconsistent output quality, or unexpected failure rates. Traditional A/B testing addresses this but splits your userbase, exposing real users to an unproven model.

**Shadow Deployment solves this.** It mirrors production traffic to a challenger model running entirely in the background. Your users always receive responses from your trusted primary (control) model. The challenger processes the same prompts in parallel, invisibly, and its outputs are recorded, evaluated, and compared against the primary.

This framework implements that pattern end-to-end with:
- A **FastAPI gateway** that routes and forks traffic
- A **Redis queue** for decoupled, non-blocking background processing
- A **Python evaluation worker** that computes quality and statistical metrics
- A **SQLite database** for durable, restartable experiment history
- A **Prometheus + Grafana** observability stack for real-time dashboards
- Support for **any LLM API provider** (Groq, OpenAI, Gemini, Anthropic, etc.) via `litellm`

---

## Core Architecture

```
                        [ Client Request ]
                                │
                                ▼
                  ┌─────────────────────────────┐
                  │    FastAPI API Gateway       │
                  │  (Auth + Metrics Middleware) │
                  └──────────────┬──────────────┘
                                 │
           ┌─────────────────────┴──────────────────────┐
           ▼  Synchronous (user-facing)                  ▼  Asynchronous (background)
  ┌─────────────────┐                          ┌──────────────────────┐
  │  Control Model  │                          │   Challenger Model   │
  │  (Primary API)  │                          │    (Shadow API)      │
  │  e.g. Groq      │                          │  e.g. Gemini         │
  └────────┬────────┘                          └──────────┬───────────┘
           │                                              │
           ▼                                              ▼
  ┌─────────────────┐                          ┌──────────────────────┐
  │  Instant User   │                          │   Execution Record   │
  │   Response      │                          │  (prompt + response  │
  └─────────────────┘                          │   + latency_ms)      │
                                               └──────────┬───────────┘
                                                          │
                                                          ▼
                                               ┌──────────────────────┐
                                               │     Redis Queue      │
                                               │  (llm_shadow_queue)  │
                                               └──────────┬───────────┘
                                                          │
                                                          ▼
                                               ┌──────────────────────┐
                                               │  Evaluation Worker   │
                                               │   (Python Process)   │
                                               └──────┬───────┬───────┘
                                                      │       │
                                           ┌──────────┘       └──────────┐
                                           ▼                              ▼
                                  ┌────────────────┐           ┌──────────────────┐
                                  │  Prometheus    │           │ SQLite Database  │
                                  │  (Metrics)     │           │ (Audit History)  │
                                  └───────┬────────┘           └──────────────────┘
                                          │
                                          ▼
                                  ┌────────────────┐
                                  │    Grafana     │
                                  │  (Dashboard)   │
                                  └────────────────┘
```

---

## How Shadow Testing Works

**A single incoming request triggers the following sequence:**

1. **Receive** — The FastAPI gateway receives a `POST /api/v1/predict` request from a client.
2. **Route** — The router uses a deterministic SHA-256 hash of the `user_id` to decide the execution strategy:
   - **`shadow` mode** — The primary model serves the user; the challenger runs in the background.
   - **`challenger` mode** — The user is silently served by the challenger model (A/B split, if configured).
   - **`control` mode** — Only the primary model runs (shadow disabled globally).
3. **Execute Primary** — The control model (e.g., Groq Llama) is called synchronously. The response is immediately returned to the user. Total user-facing latency is determined solely by this step.
4. **Fork Shadow** — A `BackgroundTask` is created, firing the challenger model call (e.g., Gemini) asynchronously. The user has already received their response and is unaffected.
5. **Enqueue** — Once the challenger responds, the full payload (prompt, both responses, both latencies) is pushed to a Redis list (`llm_shadow_queue`).
6. **Evaluate** — The independently running evaluation worker pops the payload from the queue, computes quality and statistical metrics, and persists the result to SQLite.
7. **Observe** — Prometheus scrapes the `/metrics` endpoint every 5 seconds. Grafana visualizes latency, cost, and circuit breaker health in real time.

---

## Project Structure

```
.
├── config/
│   ├── router_config.yaml        # Live experiment controls (hot-reloadable)
│   └── prometheus.yml            # Prometheus scrape target configuration
│
├── deployments/
│   ├── docker-compose.yml        # Redis, Prometheus, Grafana (resource-limited)
│   └── grafana/
│       └── provisioning/
│           ├── dashboards/
│           │   ├── dashboard.yml          # Grafana dashboard auto-provisioning config
│           │   └── shadow_dashboard.json  # Pre-built Shadow Testing Dashboard
│           └── datasources/
│               └── datasource.yml         # Prometheus datasource auto-provisioning
│
├── src/
│   ├── main.py                   # FastAPI application entrypoint
│   │
│   ├── api/
│   │   ├── v1/
│   │   │   ├── endpoints.py      # POST /api/v1/predict — primary predict endpoint
│   │   │   ├── admin.py          # GET|POST /admin/config — protected admin controls
│   │   │   └── health.py         # GET /healthz (liveness), GET /readyz (readiness)
│   │   └── middleware/
│   │       ├── auth.py           # X-Admin-Key API key verification dependency
│   │       └── metrics.py        # Prometheus metric definitions and scrape middleware
│   │
│   ├── core/
│   │   ├── config.py             # Pydantic Settings — fail-fast env variable loader
│   │   ├── exceptions.py         # Unified HTTP exception handlers
│   │   └── logging.py            # Structlog JSON logging with request trace IDs
│   │
│   ├── routing/
│   │   ├── router.py             # Routing decision engine (hot-reloads YAML config)
│   │   └── strategies.py         # Deterministic SHA-256 A/B bucket assignment
│   │
│   ├── services/
│   │   ├── llm_client.py         # litellm async client + circuit breaker + retry
│   │   └── queue_client.py       # Fire-and-forget Redis enqueue wrapper
│   │
│   ├── evaluation/
│   │   ├── evaluator.py          # Worker main loop — dequeues and processes payloads
│   │   └── metrics/
│   │       ├── statistical.py    # Welch's t-test + Cohen's d effect size
│   │       └── quality.py        # Token counting and response quality checks
│   │
│   └── storage/
│       ├── redis_store.py        # Async Redis enqueue/dequeue for the shadow queue
│       └── sqlite_store.py       # Durable SQLite persistence for evaluation history
│
├── tests/
│   ├── unit/                     # Unit tests: routing, hashing, statistics
│   └── integration/              # Integration tests: full pipeline lifecycle
│
├── .env                          # Your local secrets (gitignored)
├── .env.example                  # Safe template — commit this, not .env
├── .pre-commit-config.yaml       # ruff (lint) + mypy (type check) hooks
├── requirements.txt
└── README.md
```

---

## Component Deep-Dive

### 1. API Gateway & Predict Endpoint (`src/api/v1/endpoints.py`)

The primary endpoint is `POST /api/v1/predict`. It accepts:
```json
{
  "user_id": "usr_alpha_9921",
  "prompt": "Summarize the key principles of quantum mechanics.",
  "session_id": "sess_01J0F2991"
}
```
And immediately returns:
```json
{
  "response": "Quantum mechanics describes...",
  "routing_mode": "shadow",
  "model_used": "groq/llama-3.1-8b-instant"
}
```
The shadow invocation of the challenger model and all downstream evaluation steps happen entirely after this response has been sent.

---

### 2. Traffic Router (`src/routing/router.py`, `src/routing/strategies.py`)

The router makes a deterministic, stateful routing decision per user. The same user always lands in the same traffic bucket across requests, preventing session contamination.

**Algorithm:**
```python
hash_input = f"{user_id}{EXPERIMENT_SALT}".encode('utf-8')
bucket = int(SHA256(hash_input).hexdigest(), 16) % 100
is_challenger = bucket < (CHALLENGER_TRAFFIC_WEIGHT * 100)
```

The router hot-reloads `config/router_config.yaml` on every request. This means you can change the `challenger_traffic_weight` or flip `shadow_enabled_global` to `false` (an emergency kill switch) without restarting the application.

---

### 3. LLM Client (`src/services/llm_client.py`)

Built on top of **[litellm](https://github.com/BerriAI/litellm)**, which provides a unified interface to all major LLM API providers. Swapping the model requires only changing the model name string in `.env` — no code changes.

**Supported out of the box:** Groq, OpenAI, Google Gemini, Anthropic Claude, Azure OpenAI, Mistral, Cohere, and [100+ others](https://docs.litellm.ai/docs/providers).

Built-in resilience:
- **Timeouts:** 30s for the primary model, 60s for the shadow (shadow can tolerate more latency since it doesn't block the user).
- **Retry with exponential backoff:** Up to 3 attempts via `tenacity` on transient errors.
- **Circuit Breaker:** After 5 consecutive failures, the circuit opens and stops dispatching to the failing model for a 60-second cooldown. Prevents cascading failures and runaway error logs.
- **Synthetic fallback:** If all retries and the circuit breaker are exhausted, a mathematically-simulated synthetic response is returned so the evaluator still has data to work with.

---

### 4. Evaluation Worker (`src/evaluation/evaluator.py`)

A standalone Python process that runs independently from the API gateway. It uses `BRPOP` (blocking pop) on the Redis queue, so it consumes zero CPU when idle.

For each dequeued payload, it:
1. **Computes quality metrics** — Token count for both responses, token differential, and a check for empty/null outputs.
2. **Appends to a sliding statistical window** — Maintains the last 1,000 latency samples per arm.
3. **Runs statistical tests** — Once ≥ 30 samples have accumulated, it runs a Welch's t-test + Cohen's *d* and logs a structured warning if the latency difference is statistically significant.
4. **Persists to SQLite** — Every evaluated pair is written as a durable row in `evaluations.db`, surviving worker restarts.

---

### 5. Storage Layer

| Layer | Technology | Purpose |
|---|---|---|
| **Ephemeral Queue** | Redis (`llm_shadow_queue`) | Buffer between API gateway and evaluation worker. Fast, in-memory, non-blocking. |
| **Durable History** | SQLite (`evaluations.db`) | Permanent record of every control/challenger pair. Survives container restarts. Queryable with any SQL tool. |
| **Experiment Audit Log** | SQLite (`experiment_events`) | Records every admin configuration change (weight updates, kill-switch toggles) with timestamps. |

---

### 6. Admin API (`src/api/v1/admin.py`)

All `/admin/*` routes are protected by API key authentication (`X-Admin-Key` header). Allows dynamic experiment control without redeployment.

| Endpoint | Method | Action |
|---|---|---|
| `/admin/config` | `GET` | Retrieve current router config (traffic weights, shadow flag) |
| `/admin/config` | `POST` | Update traffic weight or toggle shadow globally |

**Example — Disable Shadow Testing Immediately:**
```bash
curl -X POST http://localhost:8000/admin/config \
  -H "X-Admin-Key: your_admin_key" \
  -H "Content-Type: application/json" \
  -d '{"shadow_enabled_global": false}'
```

---

## Metrics & Observability

The framework exposes the following Prometheus metrics at `GET /metrics`:

| Metric | Type | Labels | Description |
|---|---|---|---|
| `llm_request_latency_seconds` | Histogram | `model_name`, `routing_mode` | Full request duration per model, broken down by traffic type |
| `llm_token_cost_dollars_total` | Counter | `model_name` | Simulated cost accumulator based on token counts |
| `llm_circuit_breaker_state` | Gauge | `model_name` | `0` = closed (healthy), `1` = open (failing) |

Prometheus scrapes every **5 seconds**. The pre-built Grafana dashboard auto-provisions at startup and shows:
- **LLM Latency (avg over 1m)** — Time-series line graph comparing all models
- **Circuit Breaker State** — Stat panel with red/green threshold indicators
- **Total Simulated Cost ($)** — Running cost accumulator per model

---

## Prerequisites

| Dependency | Version | Purpose |
|---|---|---|
| Python | ≥ 3.11 | Application runtime |
| Docker Desktop | Latest | Runs Redis, Prometheus, Grafana |
| Groq API Key | — | Primary (control) model |
| Gemini API Key | — | Shadow (challenger) model |

---

## Quick Start

### Step 1 — Clone & Set Up the Environment

```bash
git clone <your-repo-url>
cd "Shadow Deployment and AB testing"

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
```

### Step 2 — Configure Environment Variables

```bash
copy .env.example .env
```

Open `.env` and fill in your API keys:

```env
EXPERIMENT_SALT=your_random_secure_salt_here
ADMIN_API_KEY=your_secure_admin_key_here

# LLM Configuration
PRIMARY_MODEL_NAME=groq/llama-3.1-8b-instant
SHADOW_MODEL_NAME=gemini/gemini-2.5-flash

# API Keys
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AIza...
```

### Step 3 — Start the Observability Stack

```bash
docker compose -f deployments/docker-compose.yml up -d
```

This starts Redis (port 6379), Prometheus (port 9090), and Grafana (port 3000) with memory limits.

### Step 4 — Start the API Gateway

```bash
venv\Scripts\uvicorn src.main:app --reload
```

The server starts at `http://127.0.0.1:8000`.

### Step 5 — Start the Evaluation Worker

Open a **second terminal** and run:

```bash
venv\Scripts\python -m src.evaluation.evaluator
```

The worker listens to the Redis queue and processes shadow payloads in the background.

### Step 6 — Send a Test Request

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/predict" `
  -Method Post `
  -Headers @{"Content-Type"="application/json"} `
  -Body '{"user_id": "user_alpha_001", "prompt": "Explain the concept of shadow deployment."}'
```

---

## Configuration Reference

### `.env` Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `EXPERIMENT_SALT` | ✅ | — | Random string used to seed the SHA-256 A/B bucket hash. Change this to re-randomize user assignments. |
| `ADMIN_API_KEY` | ✅ | — | Secret key for the protected `/admin/*` routes. |
| `REDIS_URL` | — | `redis://localhost:6379/0` | Redis connection URL |
| `SQLITE_DB_PATH` | — | `sqlite:///./evaluations.db` | SQLite file path |
| `PRIMARY_MODEL_NAME` | ✅ | — | litellm model string for the control (primary) model |
| `SHADOW_MODEL_NAME` | ✅ | — | litellm model string for the challenger (shadow) model |
| `SHADOW_ENABLED_GLOBAL` | — | `true` | Global switch. Set to `false` to disable all shadow forking. |
| `CHALLENGER_TRAFFIC_WEIGHT` | — | `0.5` | Fraction of traffic (0.0–1.0) to send directly to the challenger in A/B mode. |
| `GROQ_API_KEY` | — | — | API key for Groq models |
| `GEMINI_API_KEY` | — | — | API key for Google Gemini models |
| `OPENAI_API_KEY` | — | — | API key for OpenAI models |
| `ANTHROPIC_API_KEY` | — | — | API key for Anthropic Claude models |

### `config/router_config.yaml` (Hot-Reloaded)

```yaml
shadow_enabled_global: true      # Set to false for emergency kill switch
challenger_traffic_weight: 0.0   # Set > 0.0 to enable A/B split
```

Changes to this file take effect on the **next incoming request** without restarting the server.

---

## API Reference

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/` | `GET` | None | Health status ping |
| `/healthz` | `GET` | None | Liveness probe (returns 200 if process is up) |
| `/readyz` | `GET` | None | Readiness probe (checks Redis + SQLite; returns 503 if either is down) |
| `/metrics` | `GET` | None | Prometheus metrics scrape endpoint |
| `/api/v1/predict` | `POST` | None | Main prediction endpoint |
| `/admin/config` | `GET` | `X-Admin-Key` | Get current router configuration |
| `/admin/config` | `POST` | `X-Admin-Key` | Update traffic weights or kill switch |
| `/docs` | `GET` | None | Interactive Swagger UI (auto-generated by FastAPI) |

---

## Running the Evaluation Worker

The evaluation worker is a separate, long-running process. It must be started independently from the API gateway.

```bash
# In a separate terminal
venv\Scripts\python -m src.evaluation.evaluator
```

It runs indefinitely, processing one payload at a time from the Redis queue. It logs structured JSON to stdout. Once it has accumulated **≥ 30 samples** per arm, it will log statistical test results:

```json
{
  "event": "Statistically significant latency difference detected!",
  "p_value": 0.0001,
  "cohens_d": 2.4,
  "level": "warning"
}
```

All processed records are also persisted to `evaluations.db` and can be queried directly:

```sql
SELECT
  control_model,
  challenger_model,
  AVG(control_latency_ms)    AS avg_control_ms,
  AVG(challenger_latency_ms) AS avg_challenger_ms,
  COUNT(*)                   AS total_samples
FROM evaluations
GROUP BY control_model, challenger_model;
```

---

## Viewing the Grafana Dashboard

1. Open [http://localhost:3000](http://localhost:3000) in your browser.
2. The dashboard auto-provisions at startup — no login required (anonymous access is enabled by default for local development).
3. Navigate to **Dashboards → Shadow Testing Dashboard**.

The dashboard refreshes every **5 seconds** and displays:
- **LLM Latency (avg over 1m)** — A time-series chart showing average response latency per model and routing mode.
- **Circuit Breaker State** — A live stat panel showing whether each model's circuit breaker is open (red) or closed (green).
- **Total Simulated Cost ($)** — A running cost accumulator using token counts and approximate per-model pricing.

---

## Statistical Methodology

The evaluation engine uses **Welch's two-sample t-test** (not Student's pooled t-test) because LLM latency distributions between different providers are rarely equal-variance. Welch's test is the correct default when equal-variance cannot be assumed.

$$t = \frac{\bar{X}_1 - \bar{X}_2}{\sqrt{\frac{s_1^2}{n_1} + \frac{s_2^2}{n_2}}}$$

Alongside the p-value, the framework computes **Cohen's *d*** as an effect size measure. A statistically significant result (p < 0.05) alone is not sufficient to justify a model promotion decision — with a large enough sample, even a 1ms difference can become significant. Cohen's *d* contextualizes the result:

| Cohen's *d* | Interpretation |
|---|---|
| < 0.2 | Negligible — likely not worth acting on |
| 0.2 – 0.5 | Small effect |
| 0.5 – 0.8 | Medium effect |
| > 0.8 | Large effect — strong signal to promote or reject |

---

## Resilience & Safety

| Feature | Implementation |
|---|---|
| **Circuit Breaker** | After 5 consecutive failures, the challenger circuit opens for 60 seconds. Prevents a degraded API from overwhelming background queues. |
| **Retry with backoff** | Up to 3 retries with exponential backoff via `tenacity`. Only retries transient errors, never validation errors. |
| **Emergency Kill Switch** | Set `shadow_enabled_global: false` in `router_config.yaml`. Takes effect within one request — no redeploy. |
| **Admin Auth** | All traffic-control endpoints require a static API key via `X-Admin-Key` header. |
| **Fail-Fast Config** | App crashes immediately on startup if required environment variables are missing or malformed. No silent misconfigurations. |
| **Structured Logging** | Every log line carries a request trace ID, enabling end-to-end correlation of a single request across both the control and shadow execution paths. |
| **Memory-Limited Docker** | Redis (256 MB), Prometheus (512 MB), and Grafana (512 MB) all have hard memory caps in `docker-compose.yml`. |
