# Enterprise LLM Shadow Deployment & A/B Testing Framework

A production-grade, real-time shadow deployment and A/B testing framework for Large Language Models (LLMs). This framework enables engineering and research teams to safely evaluate challenger LLM APIs against a production control model — with **zero impact to end-user latency** — and make data-driven model promotion decisions backed by rigorous statistical analysis.

![CI](https://github.com/abntazim-1/Shadow-Deployment-and-A-B-testing/actions/workflows/ci.yml/badge.svg)

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
- A **FastAPI gateway** that routes and forks traffic, with per-IP rate limiting and full input validation
- A **Redis queue** (connection-pooled, with dead-letter queue) for decoupled, non-blocking background processing
- A **Python evaluation worker** with bounded concurrency (semaphore) and startup warmup from SQLite history
- A **SQLite database** (WAL mode) for durable, concurrent-safe experiment history with full lifecycle tracking
- A **Prometheus + Grafana** observability stack with LLM-appropriate histogram buckets and queue depth alerting
- **RFC 7807 Problem Details** error responses and `X-Trace-Id` header propagation for end-to-end request correlation
- **Automatic promotion signals** when statistical significance + meaningful effect size are both detected
- Support for **any LLM API provider** (Groq, OpenAI, Gemini, Anthropic, etc.) via `litellm`
- **GitHub Actions CI** that runs linting, tests, and a Docker build check on every push

---

## Core Architecture

```
                        [ Client Request ]
                                │
                     [slowapi: 60 req/min/IP]
                                │
                                ▼
                  ┌─────────────────────────────┐
                  │    FastAPI API Gateway       │
                  │  (Auth + Metrics Middleware) │
                  │  X-Trace-Id header returned  │
                  └──────────────┬──────────────┘
                                 │
           ┌─────────────────────┴──────────────────────┐
           ▼  Synchronous (user-facing)                  ▼  Asynchronous (background)
  ┌─────────────────┐                          ┌──────────────────────┐
  │  Control Model  │                          │   Challenger Model   │
  │  (Primary API)  │                          │    (Shadow API)      │
  └────────┬────────┘                          └──────────┬───────────┘
           │                                              │
           ▼                                              ▼
  ┌─────────────────┐                          ┌──────────────────────┐
  │  Instant User   │                          │  Redis Queue         │
  │  Response +     │                          │  (pooled client,     │
  │  X-Trace-Id     │                          │   dead-letter queue) │
  └─────────────────┘                          └──────────┬───────────┘
                                                          │
                                                          ▼
                                               ┌──────────────────────┐
                                               │  Evaluation Worker   │
                                               │  (semaphore: 10x     │
                                               │   concurrent, SQLite │
                                               │   warmup on start)   │
                                               └──────┬───────┬───────┘
                                                      │       │
                                           ┌──────────┘       └──────────┐
                                           ▼                              ▼
                                  ┌────────────────┐           ┌──────────────────┐
                                  │  Prometheus    │           │ SQLite (WAL mode)│
                                  │  (LLM buckets  │           │ evaluations +    │
                                  │   queue depth) │           │ experiments +    │
                                  └───────┬────────┘           │ audit events     │
                                          │                    └──────────────────┘
                                          ▼
                                  ┌────────────────┐
                                  │    Grafana     │
                                  │  (Dashboard)   │
                                  └────────────────┘
```

---

## How Shadow Testing Works

**A single incoming request triggers the following sequence:**

1. **Rate Limit** — `slowapi` checks the caller's IP: max 60 requests/minute. Exceeding returns `429 Too Many Requests`.
2. **Receive** — The FastAPI gateway receives `POST /api/v1/predict`. Input is validated (`user_id` ≤ 256 chars, `prompt` ≤ 8000 chars).
3. **Trace** — A UUID `trace_id` is generated and bound into the structlog context. All subsequent log lines carry it automatically.
4. **Route** — The router uses a deterministic SHA-256 hash of the `user_id` to decide the execution strategy (config is cached for 5s with double-checked locking):
   - **`shadow` mode** — The primary model serves the user; the challenger runs in the background.
   - **`challenger` mode** — The user is silently served by the challenger model (full A/B split).
   - **`control` mode** — Only the primary model runs (shadow disabled globally).
5. **Execute Primary** — The control model is called synchronously via the circuit-breaker-guarded `litellm` client. The response is immediately returned to the user with an `X-Trace-Id` header.
6. **Fork Shadow** — A `BackgroundTask` fires the challenger call. The user has already received their response and is completely unaffected.
7. **Enqueue** — Once the challenger responds, the full payload is pushed to `llm_shadow_queue` in Redis.
8. **Evaluate** — The evaluation worker (up to 10 in parallel via semaphore) dequeues the payload, computes quality and statistical metrics, and persists the result to SQLite. Failed evaluations go to `llm_shadow_queue:dead_letter` — never silently dropped.
9. **Signal** — If `p < 0.05` AND `|Cohen's d| > 0.5`, a `promotion_signal` event is written to the `experiment_events` audit table automatically.
10. **Observe** — Prometheus scrapes `/metrics` every 5 seconds, including queue depth and LLM-appropriate latency histograms. Grafana visualizes it in real time.

---

## Project Structure

```
.
├── .github/
│   └── workflows/
│       └── ci.yml                # GitHub Actions: lint + test + Docker build on every push
│
├── config/
│   ├── router_config.yaml        # Live experiment controls (hot-reloadable, 5s TTL cache)
│   └── prometheus.yml            # Prometheus scrape target configuration
│
├── deployments/
│   ├── docker-compose.yml        # Redis, API gateway, evaluation worker, Prometheus, Grafana
│   └── grafana/
│       └── provisioning/
│           ├── dashboards/
│           │   ├── dashboard.yml
│           │   └── shadow_dashboard.json
│           └── datasources/
│               └── datasource.yml
│
├── src/
│   ├── main.py                   # FastAPI app: lifespan, RFC7807 error handler, rate limiter
│   │
│   ├── api/
│   │   ├── v1/
│   │   │   ├── endpoints.py      # POST /api/v1/predict — input validation, trace ID, rate limit
│   │   │   ├── admin.py          # Admin API: config, experiment lifecycle, dead-letter inspect
│   │   │   └── health.py         # GET /healthz (liveness), GET /readyz (readiness)
│   │   └── middleware/
│   │       ├── auth.py           # X-Admin-Key API key verification
│   │       └── metrics.py        # Prometheus metrics: LLM buckets, cost, CB state, queue depth
│   │
│   ├── core/
│   │   ├── config.py             # Pydantic Settings — fail-fast env variable loader
│   │   ├── exceptions.py         # HTTP exception hierarchy
│   │   └── logging.py            # Structlog JSON logging (called from lifespan, not import)
│   │
│   ├── routing/
│   │   ├── router.py             # Routing engine with 5s TTL cache + double-checked locking
│   │   └── strategies.py         # Deterministic SHA-256 A/B bucket assignment
│   │
│   ├── services/
│   │   ├── llm_client.py         # litellm async client + circuit breaker + retry
│   │   └── queue_client.py       # Fire-and-forget Redis enqueue wrapper
│   │
│   ├── evaluation/
│   │   ├── evaluator.py          # Worker: semaphore concurrency, SQLite warmup, dead-letter
│   │   └── metrics/
│   │       ├── statistical.py    # Welch's t-test + Cohen's d + promotion signal
│   │       └── quality.py        # Token counting + ROUGE-L lexical overlap
│   │
│   └── storage/
│       ├── redis_store.py        # Pooled Redis client, enqueue, dead-letter push, queue depth
│       └── sqlite_store.py       # WAL-mode SQLite: evaluations, experiments, audit events
│
├── tests/
│   ├── conftest.py               # Session-scoped temp DB fixture (test isolation)
│   ├── unit/                     # Routing, hashing, statistics, circuit breaker
│   └── integration/              # Full pipeline: predict, admin, trace ID, RFC7807, lifecycle
│
├── Dockerfile                    # python:3.11-slim production image
├── Makefile                      # dev | worker | test | lint
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
  "prompt": "Summarize the key principles of quantum mechanics."
}
```
And immediately returns:
```json
{
  "response": "Quantum mechanics describes...",
  "routing_mode": "shadow",
  "model_used": "groq/llama-3.1-8b-instant",
  "trace_id": "f3a2b1c0-..."
}
```
With response header:
```
X-Trace-Id: f3a2b1c0-...
```

The `trace_id` appears in every log line for that request, in the response body, and in the HTTP header — enabling complete end-to-end correlation from client to logs.

**Input validation** (enforced by Pydantic `Field`):
- `user_id`: 1–256 characters
- `prompt`: 1–8000 characters — prevents a single request from burning your entire LLM API budget

---

### 2. Traffic Router (`src/routing/router.py`, `src/routing/strategies.py`)

The router makes a deterministic, stateful routing decision per user. The same user always lands in the same traffic bucket, preventing session contamination.

**Algorithm:**
```python
hash_input = f"{user_id}{EXPERIMENT_SALT}".encode('utf-8')
bucket = int(SHA256(hash_input).hexdigest(), 16) % 100
is_challenger = bucket < (CHALLENGER_TRAFFIC_WEIGHT * 100)
```

The router caches `config/router_config.yaml` with a **5-second TTL** and **double-checked locking** — at 1,000 req/s, this reduces file I/O from 1,000 reads/s to 1 read every 5 seconds while staying thread-safe.

---

### 3. LLM Client (`src/services/llm_client.py`)

Built on **[litellm](https://github.com/BerriAI/litellm)**. Swap providers by changing one `.env` line — no code changes.

**Supported:** Groq, OpenAI, Google Gemini, Anthropic Claude, Azure OpenAI, Mistral, and [100+ others](https://docs.litellm.ai/docs/providers).

Built-in resilience:
- **Timeouts:** 30s for primary, 60s for shadow (shadow can tolerate more latency).
- **Retry with exponential backoff:** Up to 3 attempts via `tenacity`.
- **Circuit Breaker:** After 5 consecutive failures, the circuit opens for a 60-second cooldown. Prevents cascading failures.
- **Synthetic fallback:** Returns a simulated response so the evaluator still receives data even when all retries fail.

---

### 4. Evaluation Worker (`src/evaluation/evaluator.py`)

A long-running process (also runnable as a Docker service) that uses `BRPOP` on Redis, consuming zero CPU when idle.

**Key production features:**
- **Startup warmup:** On boot, queries the last 1,000 latency pairs from SQLite and pre-fills the statistical windows — statistical state survives restarts.
- **Bounded concurrency:** A `asyncio.Semaphore(10)` limits parallel evaluations to 10, preventing queue bursts from spawning unbounded coroutines.
- **Dead-letter queue:** Any payload that fails processing is pushed to `llm_shadow_queue:dead_letter` with the error reason and timestamp. Nothing is ever silently lost.
- **Graceful shutdown:** On `SIGTERM`, the worker gets 10 seconds to finish in-flight evaluations before exiting.

For each dequeued payload, it:
1. **Computes quality metrics** — Token counts, token differential, empty-response check, and **ROUGE-L** lexical overlap score between control and challenger responses.
2. **Appends to sliding window** — `deque(maxlen=1000)` per arm — O(1) trim, no manual pop.
3. **Runs Welch's t-test + Cohen's d** — Once ≥ 30 samples accumulated.
4. **Fires promotion signal** — If `p < 0.05` AND `|d| > 0.5`, writes a `promotion_signal` event to the audit table.
5. **Persists to SQLite** — WAL mode allows concurrent reads from the API without locking.

---

### 5. Storage Layer

| Layer | Technology | Purpose |
|---|---|---|
| **Ephemeral Queue** | Redis `llm_shadow_queue` | Pooled client (20 connections, 5s timeout, auto-retry). Non-blocking buffer between gateway and worker. |
| **Dead-Letter Queue** | Redis `llm_shadow_queue:dead_letter` | Failed evaluations stored with error + timestamp. Inspectable via `/admin/dead-letter`. |
| **Durable History** | SQLite `evaluations` table (WAL mode) | Every control/challenger pair. Concurrent-safe. Survives restarts. |
| **Experiment Lifecycle** | SQLite `experiments` table | Named experiments with control/challenger model, start time, end time, and promotion outcome. |
| **Audit Log** | SQLite `experiment_events` table | Every config change and promotion signal, timestamped. |

---

### 6. Admin API (`src/api/v1/admin.py`)

All `/admin/*` routes require `X-Admin-Key` header. Errors return **RFC 7807 Problem Details** format.

| Endpoint | Method | Description |
|---|---|---|
| `/admin/config` | `GET` | Retrieve current router config |
| `/admin/config` | `POST` | Update traffic weight or toggle shadow globally |
| `/admin/experiment/summary` | `GET` | Live Welch's t-test result from last 1,000 evaluations |
| `/admin/experiment/start` | `POST` | Register a named experiment (writes to `experiments` table) |
| `/admin/experiment/stop` | `POST` | Mark experiment complete with outcome (`promote_challenger` / `retain_control` / `inconclusive`) |
| `/admin/dead-letter` | `GET` | Inspect last 50 failed evaluation payloads |

**Example — Check live experiment status:**
```bash
curl http://localhost:8000/admin/experiment/summary \
  -H "X-Admin-Key: your_admin_key"
```
```json
{
  "status": "success",
  "samples": 847,
  "statistics": {
    "p_value": 0.0003,
    "t_statistic": -4.12,
    "cohens_d": -0.87,
    "significant": true
  }
}
```

**Example — Emergency kill switch:**
```bash
curl -X POST http://localhost:8000/admin/config \
  -H "X-Admin-Key: your_admin_key" \
  -H "Content-Type: application/json" \
  -d '{"shadow_enabled_global": false}'
```
Takes effect within **5 seconds** (TTL cache) — no redeploy needed.

---

## Metrics & Observability

| Metric | Type | Labels | Description |
|---|---|---|---|
| `llm_request_latency_seconds` | Histogram | `model_name`, `routing_mode` | LLM latency with custom buckets `[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0]` |
| `llm_token_cost_dollars_total` | Counter | `model_name` | Estimated cost using real provider pricing (Groq, Gemini, OpenAI, Anthropic) |
| `llm_circuit_breaker_state` | Gauge | `model_name` | `0` = closed (healthy), `1` = open (failing) |
| `redis_evaluation_queue_depth` | Gauge | — | Current pending items in the evaluation queue — alert if growing |

> **Note:** Histogram buckets are tuned for LLM workloads (100ms–60s range). Default Prometheus buckets cluster at sub-100ms and are meaningless for LLM latency distributions.

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
git clone https://github.com/abntazim-1/Shadow-Deployment-and-A-B-testing.git
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

PRIMARY_MODEL_NAME=groq/llama-3.1-8b-instant
SHADOW_MODEL_NAME=gemini/gemini-2.5-flash

GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AIza...
```

### Step 3 — Start the Observability Stack

```bash
docker compose -f deployments/docker-compose.yml up -d
```

Starts Redis (6379), Prometheus (9090), and Grafana (3000).

### Step 4 — Start the API Gateway

```bash
make dev
# or: uvicorn src.main:app --reload
```

### Step 5 — Start the Evaluation Worker

```bash
make worker
# or: python -m src.evaluation.evaluator
```

### Step 6 — Send a Test Request

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/predict" `
  -Method Post `
  -Headers @{"Content-Type"="application/json"} `
  -Body '{"user_id": "user_alpha_001", "prompt": "Explain shadow deployment."}'
```

### Step 7 — Run Tests

```bash
make test
# or: pytest tests/ -v
```

---

## Configuration Reference

### `.env` Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `EXPERIMENT_SALT` | ✅ | — | Seed for SHA-256 A/B bucket hash. Change to re-randomize user assignments. |
| `ADMIN_API_KEY` | ✅ | — | Secret key for all `/admin/*` routes. |
| `REDIS_URL` | — | `redis://localhost:6379/0` | Redis connection URL |
| `SQLITE_DB_PATH` | — | `sqlite:///./evaluations.db` | SQLite file path |
| `PRIMARY_MODEL_NAME` | ✅ | — | litellm model string for the control model |
| `SHADOW_MODEL_NAME` | ✅ | — | litellm model string for the challenger model |
| `SHADOW_ENABLED_GLOBAL` | — | `true` | Global kill switch for shadow forking |
| `CHALLENGER_TRAFFIC_WEIGHT` | — | `0.5` | Fraction (0.0–1.0) of traffic routed directly to challenger |
| `GROQ_API_KEY` | — | — | Groq API key |
| `GEMINI_API_KEY` | — | — | Google Gemini API key |
| `OPENAI_API_KEY` | — | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | — | — | Anthropic Claude API key |

### `config/router_config.yaml` (Hot-Reloaded)

```yaml
shadow_enabled_global: true      # false = emergency kill switch
challenger_traffic_weight: 0.0   # > 0.0 enables A/B split
```

Changes take effect within **5 seconds** — no restart required.

---

## API Reference

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/` | `GET` | None | Health status ping |
| `/healthz` | `GET` | None | Liveness probe |
| `/readyz` | `GET` | None | Readiness probe (checks Redis + SQLite) |
| `/metrics` | `GET` | None | Prometheus metrics |
| `/api/v1/predict` | `POST` | None | Main prediction endpoint (rate limited: 60/min/IP) |
| `/admin/config` | `GET/POST` | `X-Admin-Key` | Read/update router configuration |
| `/admin/experiment/summary` | `GET` | `X-Admin-Key` | Live statistical test results |
| `/admin/experiment/start` | `POST` | `X-Admin-Key` | Start a named experiment |
| `/admin/experiment/stop` | `POST` | `X-Admin-Key` | Stop experiment with outcome |
| `/admin/dead-letter` | `GET` | `X-Admin-Key` | Inspect failed evaluation payloads |
| `/docs` | `GET` | None | Interactive Swagger UI |

All error responses follow **RFC 7807 Problem Details**:
```json
{
  "type": "https://httpstatuses.com/401",
  "title": "Missing X-Admin-Key header",
  "status": 401,
  "detail": "Missing X-Admin-Key header",
  "instance": "/admin/config"
}
```

---

## Running the Evaluation Worker

```bash
make worker
```

The worker logs structured JSON to stdout. Once ≥ 30 samples accumulate, statistical results appear:

```json
{
  "event": "Statistically significant latency difference detected!",
  "p_value": 0.0001,
  "cohens_d": -0.87,
  "level": "warning"
}
```

When both significance and effect size thresholds are met, a promotion signal is logged to the database:

```json
{
  "event_type": "promotion_signal",
  "details": {
    "recommended_action": "promote_challenger",
    "p_value": 0.0001,
    "cohens_d": -0.87,
    "sample_size": 847
  }
}
```

Query evaluation history directly:

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

## Statistical Methodology

The evaluation engine uses **Welch's two-sample t-test** (not Student's pooled t-test) because LLM latency distributions between different providers are rarely equal-variance. Welch's test is the correct default when equal-variance cannot be assumed.

$$t = \frac{\bar{X}_1 - \bar{X}_2}{\sqrt{\frac{s_1^2}{n_1} + \frac{s_2^2}{n_2}}}$$

Alongside the p-value, the framework computes **Cohen's *d*** as an effect size measure. A statistically significant result (`p < 0.05`) alone is not sufficient — with enough samples, even a 1ms difference becomes significant. Cohen's *d* contextualizes whether the difference is worth acting on:

| Cohen's *d* | Interpretation |
|---|---|
| < 0.2 | Negligible |
| 0.2 – 0.5 | Small effect |
| **0.5 – 0.8** | **Medium — triggers promotion signal** |
| > 0.8 | Large — strong signal to promote or reject |

The framework automatically emits a `promotion_signal` audit event when `p < 0.05` AND `|d| > 0.5`, removing the need for manual experiment monitoring.

---

## Resilience & Safety

| Feature | Implementation |
|---|---|
| **Rate Limiting** | `slowapi`: 60 requests/minute per IP. Returns `429` with standard headers. |
| **Input Validation** | Pydantic `Field` constraints: `user_id` ≤ 256 chars, `prompt` ≤ 8000 chars. |
| **Circuit Breaker** | After 5 consecutive failures, the circuit opens for 60 seconds. Per-model state. |
| **Retry with backoff** | Up to 3 retries with exponential backoff via `tenacity`. |
| **Dead-Letter Queue** | Failed evaluations pushed to `llm_shadow_queue:dead_letter`. Inspectable via API. |
| **Emergency Kill Switch** | `shadow_enabled_global: false` in YAML. Takes effect within 5 seconds. |
| **WAL Mode SQLite** | Readers never block writers. No `database is locked` under concurrent load. |
| **Connection Pooling** | Redis client: 20-connection pool, 5s socket timeout, auto-retry on transient drops. |
| **Graceful Shutdown** | Worker drains in-flight evaluations (10s timeout) before process exits. |
| **Trace ID Propagation** | Every request gets a UUID trace_id in logs, response body, and `X-Trace-Id` header. |
| **RFC 7807 Errors** | All API errors return Problem Details JSON (`type`, `title`, `status`, `detail`, `instance`). |
| **Admin Auth** | All `/admin/*` routes require `X-Admin-Key` header. |
| **Fail-Fast Config** | App crashes immediately on startup if required env vars are missing. |
| **Structured Logging** | Structlog JSON — initialized in `lifespan`, not at import time, for clean test output. |
| **Memory-Limited Docker** | Redis 256 MB, Prometheus 512 MB, Grafana 512 MB hard caps. |
| **CI Pipeline** | GitHub Actions: `ruff` lint + `pytest` (with Redis service) + Docker build on every push. |
