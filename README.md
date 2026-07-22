# Enterprise LLM Shadow Deployment & A/B Testing Platform

A production-grade, real-time shadow deployment, canary rollout, and semantic evaluation platform for Large Language Models (LLMs). This framework enables AI engineering teams to safely evaluate challenger LLM APIs against production control models — with **zero impact to end-user latency** — and make data-driven model promotion decisions backed by automated guardrails, LLM-as-a-Judge semantic scoring, and rigorous statistical analysis.

![CI](https://github.com/abntazim-1/Shadow-Deployment-and-A-B-testing/actions/workflows/ci.yml/badge.svg)

---

## Table of Contents

1. [Overview & Core Value](#overview--core-value)
2. [Key Platform Features](#key-platform-features)
3. [Enterprise Architecture](#enterprise-architecture)
4. [How Decoupled Shadowing Works](#how-decoupled-shadowing-works)
5. [Embedded Web Management Console](#embedded-web-management-console)
6. [Automated Canary & Guardrail Engine](#automated-canary--guardrail-engine)
7. [In-Line PII Anonymization Scrubber](#in-line-pii-anonymization-scrubber)
8. [Project Structure](#project-structure)
9. [Quick Start Guide](#quick-start-guide)
10. [Configuration Reference](#configuration-reference)
11. [API Reference](#api-reference)
12. [LLM-as-a-Judge & Statistical Methodology](#llm-as-a-judge--statistical-methodology)
13. [Resilience & Security Controls](#resilience--security-controls)

---

## Overview & Core Value

Deploying a new Large Language Model to production carries real risk. A model that scores well on offline benchmarks may perform poorly under real-world traffic patterns — suffering higher latency, hallucinating structured outputs, or leaking sensitive context. Traditional A/B testing splits user traffic, directly exposing end users to unproven model regressions.

**Enterprise Shadow Deployment solves this.** Production user requests are served instantly by your trusted primary (control) model. Simultaneously, the framework asynchronously enqueues the request to an evaluation pipeline. Challenger models process the same prompts in parallel, and outputs are evaluated using **LLM-as-a-Judge semantic scoring**, **Welch's t-test statistical analysis**, and **automated guardrail checks**.

---

## Key Platform Features

- ⚡ **Decoupled API Gateway Architecture**: Shadow request payloads are pushed to a connection-pooled Redis queue in ~1ms. Gateway response time to end users is completely uninhibited by shadow execution latencies.
- 🧠 **LLM-as-a-Judge Semantic Evaluator**: Replaces obsolete string matching (ROUGE-L) with structured semantic scoring (1–5 scale) measuring factuality, tone, schema compliance, and equivalence with natural language reasoning explanations.
- 🐤 **Automated Canary & Auto-Rollback Engine**: Progressive traffic shifting (5% → 20% → 50% → 100%) with continuous safety evaluation. Automatically aborts and rolls back traffic to the control model if challenger quality scores fall below safety thresholds.
- 🔒 **In-Line PII Anonymization Scrubber**: High-performance regex engine redacting Emails, Credit Cards, SSNs, Phone Numbers, API Keys, and IP addresses before shadow payloads reach 3rd-party model providers (GDPR, HIPAA, and SOC2 compliant).
- 🖥️ **Embedded Web Management Console**: Rich single-page dashboard served at `/console` featuring side-by-side completion diffs, LLM judge reasoning inspector, live canary sliders, and an Emergency Kill Switch.
- 📊 **Prometheus & Grafana Observability**: Custom LLM-tuned latency histogram buckets (100ms–60s), token cost tracking counters, circuit breaker gauges, and queue depth alerts.
- 🛡️ **Production Resilience**: Circuit breakers via `tenacity` & custom state machines, dead-letter queue inspectability (`llm_shadow_queue:dead_letter`), WAL-mode SQLite database, and RFC 7807 Problem Details error formatting.

---

## Enterprise Architecture

```
                               ┌────────────────────────────────┐
                               │       Client Request           │
                               └───────────────┬────────────────┘
                                               │
                                    [slowapi: 60 req/min/IP]
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │     FastAPI API Gateway        │
                               │   (Auth + Trace ID Header)     │
                               └───────────────┬────────────────┘
                                               │
                      ┌────────────────────────┴────────────────────────┐
                      ▼ (Synchronous User Path)                         ▼ (Asynchronous Shadow Path)
          ┌───────────────────────┐                         ┌───────────────────────┐
          │  Primary Control LLM  │                         │  In-Line PII Scrubber │
          │  (e.g., GPT-4o / Groq)│                         │ (Email/Card/SSN/Key)  │
          └───────────┬───────────┘                         └───────────┬───────────┘
                      │                                                 │
                      ▼                                                 ▼
          ┌───────────────────────┐                         ┌───────────────────────┐
          │ Instant Response      │                         │  Redis Stream Queue   │
          │ (X-Trace-Id Header)   │                         │ (llm_shadow_queue)    │
          └───────────────────────┘                         └───────────┬───────────┘
                                                                        │
                                                                        ▼
                                                            ┌───────────────────────┐
                                                            │  Evaluation Worker    │
                                                            │ (Async Challenger Call│
                                                            │ + LLM-as-a-Judge Eval)│
                                                            └───────┬───────┬───────┘
                                                                    │       │
                                                        ┌───────────┘       └───────────┐
                                                        ▼                               ▼
                                            ┌───────────────────────┐       ┌───────────────────────┐
                                            │ SQLite / Postgres DB  │       │ Prometheus + Grafana  │
                                            │ (evaluations, canary, │       │ (LLM latency buckets, │
                                            │  audit events)        │       │  queue depth, cost)   │
                                            └───────────────────────┘       └───────────────────────┘
```

---

## Embedded Web Management Console

Access the interactive dashboard directly at **`http://localhost:8000/console`**.

### Console Capabilities:
1. **Live Side-by-Side Completion Inspector**: View Control vs. Challenger outputs side by side with execution latency comparisons, token differentials, LLM Judge quality scores (1–5 scale), semantic equivalence percentages, and judge reasoning notes.
2. **Canary & Progressive Delivery Board**: Trigger progressive traffic shifts (5% → 20% → 50% → 100%), monitor sample counts, adjust min-judge score thresholds, or step up rollouts manually.
3. **Emergency Kill Switch**: One-click global disable (`shadow_enabled_global: false`) taking effect across all gateway nodes within 5 seconds without redeployment.

---

## Automated Canary & Guardrail Engine

The Canary Engine manages progressive model promotions while protecting system stability:

```
[Start Canary (5% Weight)] ──> [Sample Evaluation Data] ──> [Evaluate LLM Judge Score]
                                                                      │
                                     ┌────────────────────────────────┴────────────────────────────────┐
                                     ▼ (Average Score >= 3.5)                                          ▼ (Average Score < 3.5)
                        [Step Up: 20% ──> 50% ──> 100%]                                   [AUTOMATED ROLLBACK TO CONTROL]
                        [Status: Completed]                                               [Weight: 0% | Status: Rolled Back]
```

### Canary API Endpoints:
- `POST /admin/canary/start`: Start progressive canary rollout.
- `GET /admin/canary/status`: Inspect live canary status and evaluate guardrails.
- `POST /admin/canary/step`: Advance canary traffic weight to the next step.
- `POST /admin/canary/rollback`: Trigger emergency or guardrail-driven rollback.

---

## In-Line PII Anonymization Scrubber

To ensure GDPR, HIPAA, and corporate data governance compliance when forwarding user prompts to third-party challenger APIs, the framework sanitizes payloads in-line:

| Sensitive Data Type | Pattern Scanned | Sanitized Replacement |
| :--- | :--- | :--- |
| **Email Addresses** | `user@domain.com` | `[EMAIL_REDACTED]` |
| **Credit Card Numbers** | `4532-XXXX-XXXX-1092` | `[CREDIT_CARD_REDACTED]` |
| **Social Security Numbers** | `XXX-XX-XXXX` | `[SSN_REDACTED]` |
| **Phone Numbers** | `+1 (555) 123-4567` | `[PHONE_REDACTED]` |
| **API Keys & Tokens** | `gsk_...`, `sk-...`, `Bearer ...` | `[API_KEY_REDACTED]` |
| **IPv4 Addresses** | `192.168.1.1` | `[IPV4_REDACTED]` |

---

## Project Structure

```
.
├── .github/
│   └── workflows/
│       └── ci.yml                # CI Pipeline: ruff lint + pytest + Docker build check
│
├── config/
│   ├── router_config.yaml        # Hot-reloadable experiment & sampling controls
│   └── prometheus.yml            # Prometheus scrape targets
│
├── deployments/
│   ├── docker-compose.yml        # Redis, API Gateway, Evaluation Worker, Prometheus, Grafana
│   └── grafana/
│       └── provisioning/         # Dashboards and Datasource provisioning
│
├── src/
│   ├── main.py                   # FastAPI Application lifespan, RFC7807 error handler, Web Console route
│   │
│   ├── api/
│   │   ├── static/
│   │   │   └── console.html      # Embedded Web Control Console (Tailwind UI)
│   │   ├── v1/
│   │   │   ├── endpoints.py      # POST /api/v1/predict (Decoupled queueing + PII scrubber)
│   │   │   ├── admin.py          # Admin endpoints: Config, Canary, Summary, Dead-letter, Recent Evals
│   │   │   └── health.py         # GET /healthz & /readyz probes
│   │   └── middleware/
│   │       ├── auth.py           # X-Admin-Key API key verification
│   │       └── metrics.py        # Prometheus telemetry (LLM buckets, cost, CB state)
│   │
│   ├── core/
│   │   ├── config.py             # Pydantic Settings fail-fast environment variable loader
│   │   ├── exceptions.py         # Structured HTTP exception hierarchy
│   │   └── logging.py            # Structlog JSON logging context binder
│   │
│   ├── routing/
│   │   ├── router.py             # SHA-256 A/B assignment router & 5s TTL cache
│   │   ├── strategies.py         # Stateful deterministic user hash bucketing
│   │   └── canary_engine.py      # Automated Canary Rollout & Auto-Rollback Engine
│   │
│   ├── security/
│   │   └── pii_scrubber.py       # High-performance regex PII anonymization scrubber
│   │
│   ├── services/
│   │   ├── llm_client.py         # litellm async completion client + circuit breaker
│   │   └── queue_client.py       # Fire-and-forget Redis enqueue wrapper
│   │
│   ├── evaluation/
│   │   ├── evaluator.py          # Worker: Decoupled shadow execution, LLM judge calls, SQLite persistence
│   │   └── metrics/
│   │       ├── statistical.py    # Welch's t-test + Cohen's d effect size
│   │       └── quality.py        # LLM-as-a-Judge semantic scoring + token metrics
│   │
│   └── storage/
│       ├── redis_store.py        # Pooled Redis client & dead-letter queue storage
│       └── sqlite_store.py       # WAL-mode SQLite database engine
│
├── tests/
│   ├── unit/                     # Circuit breaker, routing, statistics, canary, PII scrubber tests
│   └── integration/              # Full pipeline API, admin, canary, and console tests
│
├── Dockerfile                    # Production python:3.11-slim container image
├── Makefile                      # Standardized developer commands (dev, worker, test, lint)
├── requirements.txt
└── README.md
```

---

## Quick Start Guide

### Step 1 — Clone & Set Up Environment

```bash
git clone https://github.com/abntazim-1/Shadow-Deployment-and-A-B-testing.git
cd "Shadow Deployment and AB testing"

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
```

### Step 2 — Environment Configuration

```bash
copy .env.example .env
```

Configure `.env` with secure secrets and provider keys:

```env
EXPERIMENT_SALT=your_random_secure_salt_here
ADMIN_API_KEY=secret_admin_key_123

PRIMARY_MODEL_NAME=groq/llama-3.1-8b-instant
SHADOW_MODEL_NAME=gemini/gemini-2.5-flash

GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AIza...
```

### Step 3 — Launch Observability & Ephemeral Infrastructure

```bash
docker compose -f deployments/docker-compose.yml up -d
```

Starts Redis (`6379`), Prometheus (`9090`), and Grafana (`3000`).

### Step 4 — Run API Gateway & Evaluation Worker

```bash
# Terminal 1: Start API Gateway
make dev

# Terminal 2: Start Evaluation Worker
make worker
```

### Step 5 — Access Web Management Console & Send Prediction

Open **`http://localhost:8000/console`** in your web browser.

Send a test prediction request:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/predict" `
  -Method Post `
  -Headers @{"Content-Type"="application/json"} `
  -Body '{"user_id": "user_alpha_001", "prompt": "Explain enterprise shadow deployment."}'
```

### Step 6 — Run Full Test Suite

```bash
make test
# or: .\venv\Scripts\pytest tests/ -v
```

---

## Configuration Reference

### Dynamic Hot-Reload Configuration (`config/router_config.yaml`)

```yaml
shadow_enabled_global: true      # Global kill switch (false = stop all shadow calls)
challenger_traffic_weight: 0.5   # Fraction (0.0 to 1.0) routed directly to challenger
shadow_sample_rate: 1.0          # Sampling rate (0.1 = sample 10% of traffic)
```

Takes effect across all API Gateway nodes within **5 seconds** without process restarts.

---

## API Reference

| Endpoint | Method | Auth | Description |
| :--- | :--- | :--- | :--- |
| `/console` | `GET` | None | Embedded Web Management Console UI |
| `/api/v1/predict` | `POST` | None | Main prediction gateway (rate limited: 60/min/IP) |
| `/admin/canary/start` | `POST` | `X-Admin-Key` | Start progressive canary rollout |
| `/admin/canary/status` | `GET` | `X-Admin-Key` | Inspect live canary status & evaluate guardrails |
| `/admin/canary/step` | `POST` | `X-Admin-Key` | Step up canary traffic weight |
| `/admin/canary/rollback` | `POST` | `X-Admin-Key` | Trigger emergency or guardrail canary rollback |
| `/admin/evaluations/recent` | `GET` | `X-Admin-Key` | Fetch recent completions with LLM judge scores & reasoning |
| `/admin/config` | `GET/POST` | `X-Admin-Key` | Read or update router configuration |
| `/admin/experiment/summary` | `GET` | `X-Admin-Key` | Live statistical Welch's t-test summary |
| `/admin/dead-letter` | `GET` | `X-Admin-Key` | Inspect failed evaluation payloads |
| `/healthz` & `/readyz` | `GET` | None | Kubernetes Liveness and Readiness probes |
| `/metrics` | `GET` | None | Prometheus scrape metrics endpoint |

---

## LLM-as-a-Judge & Statistical Methodology

### LLM-as-a-Judge Evaluation
Rather than relying on outdated n-gram overlap algorithms (ROUGE-L/BLEU), the worker invokes an **LLM Judge model** using structured JSON output prompts to evaluate:
- **Semantic Equivalence (0.0 – 1.0)**: Intent and factual alignment.
- **Judge Quality Score (1.0 – 5.0)**: Overall output coherence, tone, and accuracy.
- **Reasoning Text**: Plain-language justification for the assigned quality score.

### Welch's t-Test & Cohen's *d* Effect Size
The statistical engine computes Welch's unequal variances t-test:

$$t = \frac{\bar{X}_1 - \bar{X}_2}{\sqrt{\frac{s_1^2}{n_1} + \frac{s_2^2}{n_2}}}$$

Alongside the p-value (`p < 0.05`), the system measures **Cohen's *d*** effect size. A `promotion_signal` event is automatically recorded in SQLite when statistical significance (`p < 0.05`) is combined with meaningful effect size (`|d| > 0.5`).

---

## Resilience & Security Controls

- **Decoupled Queueing**: API Gateway pushes raw shadow payloads to Redis Streams in ~1ms; worker handles shadow execution.
- **In-Line PII Masking**: Automatic redaction of sensitive patterns (Email, Cards, SSNs, Keys, IPs) before sending to challenger APIs.
- **Circuit Breakers**: Fast-fails to synthetic fallback after 5 consecutive failures with a 60-second cooldown period.
- **Dead-Letter Queue**: Failed worker evaluation tasks are pushed to `llm_shadow_queue:dead_letter` for inspection and replay.
- **RFC 7807 Errors**: Structured Problem Details JSON format returned for all HTTP API errors.
