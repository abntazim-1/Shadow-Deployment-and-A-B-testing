# Enterprise LLM Shadow Deployment & A/B Testing Framework: Implementation Blueprint (v2)

This is the revised implementation blueprint for a production-grade Shadow Deployment and A/B Testing framework for LLMs. It runs **entirely locally, at $0 cost**, using only open-source tools (FastAPI, Redis, Ollama, Prometheus, Grafana, SQLite) and simulated traffic where needed. Nothing in this plan requires a paid API key, cloud account, or SaaS subscription.

> **What changed from v1:** added an auth layer, resilience (timeouts/retries/circuit breaker), durable storage for evaluation history, health checks, a kill switch, resource limits, a corrected statistical test, and a lightweight local CI/quality gate. All additions use free, local tooling — the zero-cost constraint is preserved throughout.

---

## 1. System Architecture & Component Topology

```
                      [ Client Request ]
                              │
                              ▼
                ┌───────────────────────────┐
                │    API Gateway / Router   │
                │  (FastAPI / Async + Auth) │
                └──────────────┬────────────┘
                               │
         ┌─────────────────────┴─────────────────────┐
         ▼ (Synchronous)                             ▼ (Asynchronous Background)
┌─────────────────┐                        ┌──────────────────┐
│  Control Model  │                        │ Challenger Model │
│  (Production)   │                        │     (Shadow)     │
└────────┬────────┘                        └─────────┬────────┘
         │                                           │
         ▼ (Instant Token Stream)                    ▼
┌─────────────────┐                        ┌──────────────────┐
│   Live User     │                        │ Execution Record │
└─────────────────┘                        └─────────┬────────┘
                                                     │
                                                     ▼
                                           ┌──────────────────┐
                                           │   Redis Queue    │
                                           └─────────┬────────┘
                                                     │
                                                     ▼
                                           ┌──────────────────┐
                                           │ Evaluation Worker│
                                           │ (Python Engine)  │
                                           └─────────┬────────┘
                                                     │
                                       ┌─────────────┼─────────────┐
                                       ▼             ▼             ▼
                               ┌───────────┐ ┌───────────┐ ┌──────────────┐
                               │Prometheus │ │  Grafana  │ │ SQLite (audit│
                               │ (Metrics) │ │(Dashboard)│ │ & history)   │
                               └───────────┘ └───────────┘ └──────────────┘
```

The SQLite addition is the one structural change: it's the durable record of every control/challenger pair and every statistical test result, so a worker or container restart doesn't wipe your experiment history. SQLite is a single file, requires no server process, and costs nothing.

---

## 2. Directory Matrix (File Structure)

```text
llm-shadow-testing/
│
├── config/
│   ├── router_config.yaml         # Live weights, fallback rules, feature flags (hot-reloadable)
│   └── prometheus.yml             # Prometheus scrape targets
│
├── deployments/
│   ├── docker-compose.yml         # Redis, Prometheus, Grafana — with memory limits
│   └── grafana/
│       └── provisioning/
│
├── src/
│   ├── __init__.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── v1/
│   │   │   ├── endpoints.py       # /api/v1/predict
│   │   │   ├── admin.py           # Admin routes — now behind API-key auth
│   │   │   └── health.py          # NEW: /healthz, /readyz
│   │   └── middleware/
│   │       ├── metrics.py         # Telemetry hookups
│   │       └── auth.py            # NEW: API key verification dependency
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # Env validation loader (Pydantic Settings)
│   │   ├── exceptions.py          # Unified exception handlers
│   │   └── logging.py             # NEW: structured JSON logging with request/trace IDs
│   │
│   ├── routing/
│   │   ├── __init__.py
│   │   ├── router.py              # Execution strategy per transaction
│   │   └── strategies.py          # Deterministic hashing engine
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── llm_client.py          # Non-blocking Ollama client — now with timeout + retry + circuit breaker
│   │   └── queue_client.py        # Fire-and-forget payload broker
│   │
│   ├── evaluation/
│   │   ├── __init__.py
│   │   ├── evaluator.py           # Orchestrates processing of data pairs
│   │   └── metrics/
│   │       ├── statistical.py     # Welch's t-test + effect size (Cohen's d)
│   │       └── quality.py         # Token parsing, semantic integrity checks
│   │
│   └── storage/
│       ├── __init__.py
│       ├── redis_store.py         # Ephemeral queue/cache
│       └── sqlite_store.py        # NEW: durable evaluation history + audit log
│
├── tests/
│   ├── unit/                      # Routing, hashing, statistics
│   └── integration/                # Full pipeline lifecycle
│
├── .pre-commit-config.yaml        # NEW: ruff + mypy + pytest hooks (all free, local)
├── .env.example
├── README.md
└── requirements.txt
```

---

## 3. Detailed Component Specifications

### Module 1: Core Bootstrapper & Configuration Engine
* **File:** `src/core/config.py`
* **Purpose:** Load and validate all runtime configuration from environment variables using Pydantic Settings before the app is allowed to start.
* **Inputs:** `.env` variables.
* **Outputs:** A single immutable `Settings` singleton.
* **Rule:** The process must crash immediately (fail-fast) if any required variable is missing or fails type validation — no silent defaults for things like ports, model names, or the experiment salt.

### Module 2: The Adaptive Traffic Splitter & Router
* **File:** `src/routing/router.py` & `src/routing/strategies.py`
* **Purpose:** Classify each request into baseline, A/B variant, or shadow-fork execution.
* **Inputs:**
    ```json
    {
      "user_id": "usr_alpha_9921",
      "prompt": "Synthesize a core summary of classical thermodynamics.",
      "session_id": "sess_01J0F2991"
    }
    ```
* **Outputs:**
    ```json
    {
      "routing_mode": "shadow",
      "primary_model_name": "phi3:latest",
      "primary_url": "http://localhost:11434/api/generate",
      "shadow_enabled": true,
      "shadow_model_name": "llama3:latest",
      "shadow_url": "http://localhost:11434/api/generate"
    }
    ```
* **Rules:**
    * **A/B assignment:** `SHA-256(user_id + EXPERIMENT_SALT)`, cast to int, `mod 100`. If below `CHALLENGER_TRAFFIC_WEIGHT * 100`, assign to the experimental variant. This is deterministic per user, so the same user always lands in the same bucket.
    * **Shadow assignment:** production path serves the user; `shadow_enabled = true` triggers the background fork.
    * **NEW — Kill switch:** `router_config.yaml` includes a top-level `shadow_enabled_global: false` flag, hot-reloaded on file change (e.g. via `watchdog` or a simple TTL cache check). Flipping this to `false` disables all shadow forking within one poll interval, without a redeploy — your emergency stop if a challenger model misbehaves.

### Module 3: Non-Blocking Network Clients (Outbound)
* **File:** `src/services/llm_client.py`
* **Purpose:** Abstract connections to local Ollama runtimes.
* **Inputs:** Target URL, prompt, temperature/params.
* **Outputs:** Async token stream.
* **Rules:**
    * If Ollama isn't reachable, fall back to a synthetic response streamer using an exponential distribution for simulated time-to-first-token ($ttft \sim \text{Exp}(\lambda)$) — no real compute cost.
    * **NEW — Timeouts:** every call to Ollama has an explicit timeout (e.g. 30s primary, 60s shadow — shadow can tolerate more latency since it's non-blocking for the user).
    * **NEW — Retry with backoff:** use `tenacity` for up to 2 retries with exponential backoff on transient connection errors only (never retry on 4xx-equivalent validation errors).
    * **NEW — Circuit breaker:** if the challenger model fails N consecutive times (e.g. 5), stop dispatching shadow traffic to it for a cooldown window and log a warning — this prevents a broken challenger from silently piling up failed background tasks forever.

### Module 4: Asynchronous Queue Pipeline
* **File:** `src/services/queue_client.py` & `src/storage/redis_store.py`
* **Purpose:** Buffer execution records into Redis without blocking the request thread.
* **Inputs:** Trace ID, prompt, full model payload for both control and challenger.
* **Outputs:** Boolean confirmation of enqueue into `llm_shadow_queue`.
* **Rule:** Must run via `asyncio.create_task` (or FastAPI background tasks) — zero added latency on the client-facing path.
* **NEW — Graceful shutdown:** on `SIGTERM`, stop accepting new enqueues, but let in-flight shadow tasks and queue drains finish (bounded by a shutdown timeout, e.g. 10s) rather than dropping them.

### Module 5: Independent Evaluation & Statistical Analytics Worker
* **File:** `src/evaluation/evaluator.py` & `src/evaluation/metrics/statistical.py`
* **Purpose:** Read queued records, compute quality/latency metrics, and persist results.
* **Inputs:**
    ```json
    {
      "request_id": "uuid-v4-trace-string",
      "prompt": "Provide three features of Python.",
      "control_response": "Readable syntax, interpreted execution, dynamic typing.",
      "control_latency_ms": 110.5,
      "challenger_response": "Easy code structure, slow performance profile, highly modular design.",
      "challenger_latency_ms": 94.2
    }
    ```
* **Outputs:** Rows written to SQLite (`evaluations` table) — not just in-memory arrays, so history survives restarts.
* **Rules:**
    * Once records exceed `MIN_STATISTICAL_SAMPLES` (recommend ≥30 per arm as a practical floor), run a statistical comparison.
    * **CHANGED — Use Welch's t-test, not the standard pooled t-test:**
      $$t = \frac{\bar{X}_1 - \bar{X}_2}{\sqrt{\frac{s_1^2}{n_1} + \frac{s_2^2}{n_2}}}$$
      This is actually the same formula you had, but it should be paired with the **Welch–Satterthwaite degrees-of-freedom correction** rather than assuming equal variances — LLM latency distributions between two different models are rarely equal-variance, and using the standard pooled-variance t-test there understates the real uncertainty. `scipy.stats.ttest_ind(..., equal_var=False)` gives you this for free.
    * **NEW — Report effect size alongside p-value:** compute Cohen's d. A p-value alone tells you "is there a difference," not "does it matter" — with enough samples, trivial differences become statistically significant. Log both.
    * Flag significance at $p < 0.05$, but surface the effect size next to it so a human can judge practical relevance.

### Module 6: Prometheus/Grafana Instrumentation Node
* **File:** `src/api/middleware/metrics.py`
* **Metrics Tracked:**
    * `llm_request_latency_seconds` (histogram, by model + traffic category)
    * `llm_token_cost_dollars` (running counter via `tiktoken` — useful even at $0 real spend, since it shows what you'd be paying if these were hosted models)
    * `llm_faithfulness_score` (rolling summary)
    * **NEW:** `llm_circuit_breaker_state` (gauge: 0 closed / 1 open) so you can see challenger health on the dashboard itself.

### Module 7 (NEW): Auth & Admin Protection
* **File:** `src/api/middleware/auth.py`, `src/api/v1/admin.py`
* **Purpose:** Prevent unauthenticated access to traffic-weight controls and manual promotion endpoints.
* **Approach:** A simple static API-key check via FastAPI `Depends()`, key read from `.env` (`ADMIN_API_KEY`). This is intentionally lightweight — no OAuth server, no paid identity provider — but it closes the "anyone on localhost can repoint production traffic" gap.
* **Rule:** All routes under `/admin` require the header `X-Admin-Key`; the public `/predict` route does not.

### Module 8 (NEW): Health Checks
* **File:** `src/api/v1/health.py`
* **Endpoints:**
    * `GET /healthz` — liveness: process is up, returns 200 immediately.
    * `GET /readyz` — readiness: checks Redis connection and SQLite file accessibility; returns 503 if either is down.
* **Why it matters even locally:** these are what your `docker-compose` healthcheck directives and any future orchestration (even a simple restart script) key off of.

### Module 9 (NEW): Structured Logging
* **File:** `src/core/logging.py`
* **Approach:** `structlog` configured to emit JSON lines with a request/trace ID bound to every log line in a request's lifecycle. Free, local, and makes debugging the async shadow path far easier than print statements — you can grep a single trace ID across the control and shadow paths.

---

## 4. Phased Execution Roadmap

### Phase 1: Local Environment & Skeleton
1. Build the directory structure from Section 2.
2. Copy `.env.example` → `.env`, fill in local values (`EXPERIMENT_SALT`, `ADMIN_API_KEY`, model names/ports).
3. Implement `src/core/config.py` with Pydantic Settings, fail-fast validation.
4. Implement `src/core/logging.py` (structlog JSON config) early — every later module logs through it.

### Phase 2: Routing Gateway
1. Build `/api/v1/predict` in `src/api/v1/endpoints.py`.
2. Implement the SHA-256 hashing split in `src/routing/strategies.py`.
3. Implement the shadow-fork-to-background pattern so the primary response returns immediately.
4. Add the `shadow_enabled_global` kill switch with hot-reload.

### Phase 3: Resilience & Outbound Clients
1. Build `src/services/llm_client.py` with timeout, `tenacity` retry, and circuit breaker.
2. Build the synthetic fallback streamer for when Ollama isn't running.
3. Unit test: force Ollama offline, confirm fallback engages and circuit breaker opens after N failures.

### Phase 4: Storage — Queue + Durable History
1. Build `src/storage/redis_store.py` for the ephemeral queue.
2. Build `src/storage/sqlite_store.py` — schema for `evaluations` and `experiment_events` (audit trail of weight changes, kill-switch toggles).
3. Confirm a worker restart doesn't lose queued-but-unprocessed records (Redis persistence via AOF, still free/local) or historical evaluations (SQLite file).

### Phase 5: Evaluation & Statistics
1. Build `src/evaluation/evaluator.py`.
2. Build `src/evaluation/metrics/statistical.py` using `scipy.stats.ttest_ind(equal_var=False)` + Cohen's d.
3. Build `src/evaluation/metrics/quality.py` for token/semantic checks.

### Phase 6: Telemetry & Dashboards
1. `docker-compose.yml` for Redis, Prometheus, Grafana — **add `mem_limit` / `deploy.resources.limits` on each service** so the stack can't consume unbounded local RAM.
2. Point `config/prometheus.yml` at `http://localhost:8000/metrics`.
3. `docker compose up -d` — confirm no cost/credit prompts anywhere in the stack.
4. Open `http://localhost:3000`, build the Control vs. Challenger panel, add the circuit-breaker-state panel.

### Phase 7 (NEW): Security & Health
1. Implement `src/api/middleware/auth.py`; lock down `/admin/*`.
2. Implement `/healthz` and `/readyz`; wire into `docker-compose` healthcheck blocks.
3. Confirm `.env` is in `.gitignore` — never commit real secrets, even local ones, out of habit.

### Phase 8 (NEW): Local Quality Gate
1. Add `.pre-commit-config.yaml` with `ruff` (lint) and `mypy` (type check) — both free, run entirely locally, no CI service required.
2. Write `tests/unit` and `tests/integration` per Section 5 below; run via `pytest`.
3. Optional, still free: a GitHub Actions workflow on push (GitHub's free tier covers this for public/private repos within generous limits) to run the same pytest suite — skip this if you'd rather stay 100% local with no external service at all.

---

## 5. System Integration Testing Protocols

1. **Thread Concurrency Verification:** Inject an artificial 5000ms delay into the Challenger code path. Confirm via logs that the client-facing response still resolves immediately — if it takes several seconds, the async boundary is broken.
2. **Stateful Hashing Verification:** Issue 50 sequential requests under one fixed `user_id`. Confirm the routing assignment never flips mid-session.
3. **Statistical Core Verification:** Feed 150 matched metric pairs through the worker. Confirm it produces both a Welch's t-test p-value and a Cohen's d, and that the numbers match an independent calculation (e.g. cross-check with `scipy` directly in a notebook).
4. **NEW — Resilience Verification:** Kill the Ollama process mid-run. Confirm: (a) the synthetic fallback engages without crashing the app, (b) the circuit breaker opens after the configured failure threshold, (c) it logs a clear structured warning.
5. **NEW — Durability Verification:** Populate some evaluation history, then restart the worker container. Confirm prior results are still present in SQLite and the dashboard reflects the full history, not just post-restart data.
6. **NEW — Kill Switch Verification:** Flip `shadow_enabled_global` to `false` in the config file while the app is running. Confirm shadow forking stops within one reload interval without restarting the process.
7. **NEW — Auth Verification:** Call an `/admin` route with no key (expect 401), a wrong key (expect 401), and the correct key (expect 200).

---

## 6. Cost Confirmation

Everything in this plan runs on your local machine or on infrastructure with a genuinely free tier:
* **Models:** Ollama, running local open-weight models (phi3, llama3, etc.) — no API billing.
* **Queue/cache:** Redis via Docker, local.
* **Metrics/dashboards:** Prometheus + Grafana via Docker, local.
* **Durable storage:** SQLite, a local file — no hosted database.
* **Quality gates:** ruff, mypy, pytest — all local CLI tools.
* **Optional CI:** GitHub Actions free tier, only if you want it; skip entirely and stay local-only if you prefer zero external dependencies.

No step in this plan requires a credit card.
