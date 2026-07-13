# Project Audit: Enterprise LLM Shadow Deployment & A/B Testing Framework

> **Audit date:** 2026-07-12 | **Files reviewed:** 28 | **Scope:** Full codebase, tests, infrastructure, docs

---

## Executive Summary

This is a **genuinely strong portfolio project**. The core loop — HTTP gateway → background fork → Redis queue → evaluation worker → SQLite persistence → Prometheus/Grafana — is well-architected and demonstrates production-grade thinking. The statistical methodology (Welch's t-test + Cohen's d), the circuit breaker, the hot-reloadable kill switch, and the litellm abstraction are all real engineering choices you can defend in depth.

That said, there are concrete correctness issues, architectural gaps, and missing signal that a senior engineer or recruiter would immediately notice. This audit surfaces all of them — from critical bugs to polish.

---

## ✅ What's Already Great (Don't Break This)

| Area | What's Good |
|---|---|
| **Architecture** | Clean separation: API gateway, routing engine, queue, worker, storage — each in its own module |
| **Statistical rigor** | Welch's t-test + Cohen's d is the correct choice and you can explain *why* (unequal variance) |
| **Circuit breaker** | Half-open state logic (`threshold - 1`) is textbook FSM implementation |
| **Litellm abstraction** | True vendor-agnostic design; one `.env` line change to swap providers |
| **Deterministic hashing** | SHA-256 + salt for A/B assignment is stateless and correct |
| **Pydantic fail-fast** | App crashes on startup for bad config — prevents silent misconfigurations |
| **Pre-commit hooks** | `ruff` + `mypy` shows professional dev hygiene |
| **Health probes** | `/healthz` (liveness) + `/readyz` (readiness with Redis + SQLite checks) is k8s-ready |
| **README** | One of the best READMEs you'll have in a portfolio — architecture diagram, methodology, runbook |

---

## 🔴 Critical — Correctness Issues (Fix These First)

### 1. `test_llm_client.py` — Argument Order Bug

**File:** `tests/unit/test_llm_client.py`, line 18 & 25

The `generate_completion` function signature is:
```python
async def generate_completion(model_name: str, prompt: str, is_shadow: bool = False) -> str:
```

But the test calls it with **three positional args in the wrong order:**
```python
# WRONG — passes `bad_url` as `prompt`, `prompt` as... nothing
res = await generate_completion(model_name, bad_url, prompt)
```

This test is testing the wrong thing and would pass for the wrong reasons. **The test never actually exercises the circuit breaker correctly.**

**Fix:**
```python
res = await generate_completion(model_name, prompt)
```
The `bad_url` concept doesn't apply here — the circuit breaker tracks `model_name`, not URL. The test should force failures by mocking `litellm.acompletion` to raise exceptions.

---

### 2. `auth.py` — Double Import

**File:** `src/api/middleware/auth.py`, line 1

```python
from fastapi import Security, Security  # Security imported twice
```

This is a linting error that `ruff` should have caught but may not if pre-commit wasn't enforced. It's a minor issue but visible in any code review.

**Fix:**
```python
from fastapi import Security
```

---

### 3. In-Memory Latency Windows Are Not Thread-Safe or Persistent

**File:** `src/evaluation/evaluator.py`, lines 10-12

```python
control_latencies: List[float] = []
challenger_latencies: List[float] = []
```

**Problems:**
- These are module-level globals. They reset to `[]` every time the worker process restarts. For a project whose selling point is "durable experiment history," the statistical state doesn't survive restarts.
- Using `list.pop(0)` to trim the window is O(n) — a `collections.deque(maxlen=1000)` is O(1).
- If you ever run multiple worker instances (horizontal scaling), they each maintain separate, divergent windows.

**Fix (immediate — use deque):**
```python
from collections import deque
control_latencies: deque = deque(maxlen=1000)
challenger_latencies: deque = deque(maxlen=1000)
# Remove the manual pop(0) logic — deque handles it automatically
```

**Fix (architectural — persist window to SQLite):**
Query the last N latency values from `evaluations` on worker startup instead of starting from a blank slate.

---

### 4. Config File Race Condition in `admin.py`

**File:** `src/api/v1/admin.py`, lines 29-41

The `update_config` endpoint does a **read-modify-write on a YAML file with no locking.** If two admin requests arrive simultaneously, one write can clobber the other.

```python
# READ
current_config = yaml.safe_load(f)
# ... time passes, another request reads the same file ...
# WRITE — overwrites what the other request wrote
yaml.safe_dump(current_config, f)
```

**Fix:** Use a `threading.Lock` or `asyncio.Lock` around the read-modify-write:
```python
import threading
_config_lock = threading.Lock()

def update_config(config_update: RouterConfig):
    with _config_lock:
        # read, modify, write
```

---

## 🟠 High Impact — Architectural Gaps

### 5. No Automatic Model Promotion/Rejection Decision

This is the biggest missing feature. The entire point of the framework is to produce a **promotion decision** — but it never actually makes one. The worker logs a warning when significance is detected, but nothing happens after that.

A recruiter will ask: *"So after you detect a statistically significant difference — what does the system do?"*

**What to add:** An `auto_promotion` module that, when `p < 0.05` AND `cohens_d > 0.5` (medium effect size), either:
- Logs a structured `PROMOTE_CANDIDATE` event to the `experiment_events` table with a summary
- Exposes a `/admin/experiment/summary` endpoint that returns the current statistical verdict
- (Optional) Calls the admin API to flip the challenger weight to 1.0 (full promotion)

Even the first option — a structured event in the audit log — is enough to close this gap narratively.

```python
# In evaluator.py, after running Welch's t-test:
if stats_result and stats_result.get("significant") and abs(stats_result.get("cohens_d", 0)) > 0.5:
    winner = "control" if stats_result["cohens_d"] > 0 else "challenger"
    log_experiment_event("promotion_signal", {
        "recommended_action": f"promote_{winner}",
        "p_value": stats_result["p_value"],
        "cohens_d": stats_result["cohens_d"],
        "sample_size": len(control_latencies)
    })
```

---

### 6. No `/admin/experiment/summary` Endpoint

Right now there's no way to **query the current experiment status** via the API. A data scientist or PM would need to open a SQL client to see any results.

**Add to `admin.py`:**
```python
@router.get("/experiment/summary")
def get_experiment_summary():
    """Returns the current statistical state of the running experiment."""
    # Query SQLite for last N records, run Welch's t-test, return results
```

This makes the system self-documenting from the API level — you can `curl` it and see if your challenger model is ready to promote.

---

### 7. Quality Metrics Are Too Shallow

**File:** `src/evaluation/metrics/quality.py`

The current "quality metrics" only measure token count difference and whether the response is empty. A recruiter familiar with LLMOps will notice this is not a real quality evaluation.

**Improvements (in order of effort):**

| Metric | Library | Effort |
|---|---|---|
| **ROUGE-L score** (lexical overlap between control and challenger) | `rouge-score` | Low |
| **Cosine similarity** (semantic similarity via sentence embeddings) | `sentence-transformers` | Medium |
| **LLM-as-a-judge** (use a cheap model to score responses 1-5) | `litellm` (already installed) | Medium |

Even just ROUGE-L adds a real quality signal and demonstrates knowledge of NLP evaluation methodology. It's also very cheap to compute.

```python
from rouge_score import rouge_scorer

def calculate_quality_metrics(prompt, control_resp, challenger_resp):
    scorer = rouge_scorer.RougeScorer(['rougeL'], use_stemmer=True)
    scores = scorer.score(control_resp, challenger_resp)
    rouge_l = scores['rougeL'].fmeasure
    # ... rest of existing metrics
    return {**existing_metrics, "rouge_l": rouge_l}
```

---

### 8. The Worker Has No Startup Warmup

**File:** `src/evaluation/evaluator.py`

When the worker starts cold, it begins with empty latency windows. It needs 30 samples before it can run any statistics. If you restart the worker mid-experiment, you lose all accumulated state.

**Fix:** On worker startup, pre-populate the sliding window from the last 1,000 records in SQLite:

```python
async def warmup_from_sqlite():
    """Pre-populate latency windows from durable SQLite history on startup."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT control_latency_ms, challenger_latency_ms FROM evaluations ORDER BY id DESC LIMIT 1000"
    ).fetchall()
    for row in reversed(rows):  # oldest first
        control_latencies.append(row[0])
        challenger_latencies.append(row[1])
    logger.info("Warmed up latency windows from SQLite", samples=len(control_latencies))
```

---

### 9. No Rate Limiting on the Public `/api/v1/predict` Endpoint

The predict endpoint has no rate limiting. Anyone can spam it with requests, exhausting your LLM API quota or Redis memory. This is a basic production concern.

**Fix:** Add `slowapi` rate limiting:
```bash
pip install slowapi
```
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("/predict")
@limiter.limit("60/minute")
async def predict(request: Request, ...):
```

---

## 🟡 Medium Priority — Quality & Completeness

### 10. `requirements.txt` Has No Pinned Versions

```
fastapi>=0.103.0
```

Lower bounds only, no upper bounds or exact pins. This means `pip install` in 6 months could produce a broken environment because of a breaking change in any dependency (e.g., litellm has frequent breaking changes).

**Fix:** After setting up your venv, generate a locked requirements file:
```bash
pip freeze > requirements.lock
```
Keep `requirements.txt` for loose human-readable deps and `requirements.lock` for reproducible installs. Mention this split in the README.

---

### 11. The Evaluation Worker Is Not Containerized

The README says to run the worker in a second terminal:
```bash
python -m src.evaluation.evaluator
```

This is a manual step that breaks the "production-ready" narrative. If you restart your machine, you must remember to restart the worker.

**Fix:** Add the worker as a service to `docker-compose.yml`:
```yaml
evaluation-worker:
  build: .
  command: python -m src.evaluation.evaluator
  depends_on:
    - redis
  environment:
    - REDIS_URL=redis://redis:6379/0
  restart: unless-stopped
```
This also requires a `Dockerfile`. Adding one is a significant portfolio signal.

---

### 12. No `Dockerfile` for the API Gateway

For the same reason as above — the API gateway has no `Dockerfile`. Every containerized backend project should have one.

**Minimum viable `Dockerfile`:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY config/ ./config/
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

### 13. `main.py` Never Calls `init_db()` on Startup

**Files:** `src/main.py`, `src/storage/sqlite_store.py`

SQLite's `init_db()` is called at module import time (line 87 of `sqlite_store.py`). This works but is an anti-pattern — it runs when the module is *imported*, not when the app *starts*, which breaks test isolation.

**Fix:** Use FastAPI's `lifespan` pattern:
```python
from contextlib import asynccontextmanager
from src.storage.sqlite_store import init_db
from src.evaluation.evaluator import run_worker

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    worker_task = asyncio.create_task(run_worker())
    yield
    # Shutdown
    worker_task.cancel()

app = FastAPI(title="Shadow Deployment & A/B Testing Framework", lifespan=lifespan)
```

This is also how you'd embed the evaluation worker in the same process if you don't want a separate terminal, and it enables graceful shutdown.

---

### 14. The Grafana Dashboard Is Too Minimal

The current dashboard has 3 panels: latency, circuit breaker, cost. For a project focused on A/B testing, the dashboard should tell the experiment story:

**Missing panels:**
- **Request distribution by routing_mode** (control vs. shadow vs. challenger) — a pie chart from Prometheus labels
- **P-value over time** — would require persisting the statistical test result as a Prometheus gauge, but even a static "current p-value" stat panel is powerful
- **Cohen's d over time** — same idea
- **Total evaluation count** — how many shadow pairs have been evaluated

Adding these requires ~30 lines of Prometheus metric additions and new dashboard JSON. High visual impact for a recruiter demo.

---

### 15. Hot-Reload of `router_config.yaml` Opens the Config File on Every Single Request

**File:** `src/routing/router.py`, lines 15-23

```python
def determine_route(user_id: str) -> RoutingDecision:
    dynamic_config = load_router_config()  # Opens + reads YAML on every request!
```

At 1,000 req/s this is 1,000 file I/O operations per second. This is a performance problem at scale.

**Fix:** Cache the config with a TTL (e.g., 5 seconds):
```python
import time

_config_cache: dict = {}
_cache_loaded_at: float = 0.0
CONFIG_TTL_SECONDS = 5.0

def load_router_config() -> dict:
    global _config_cache, _cache_loaded_at
    if time.time() - _cache_loaded_at < CONFIG_TTL_SECONDS:
        return _config_cache
    # ... file read ...
    _cache_loaded_at = time.time()
    return _config_cache
```

---

### 16. No `conftest.py` — Tests Share State

**Files:** `tests/`

There's no `conftest.py`. This means:
- No shared fixtures (the integration test re-creates the `TestClient` at module level, which means the SQLite database is initialized on import — could conflict between test runs)
- No test database isolation — tests write to the real `evaluations.db`

**Fix:** Add `tests/conftest.py`:
```python
import pytest
import os

@pytest.fixture(autouse=True, scope="session")
def use_test_db(tmp_path_factory):
    """Redirect SQLite to a temp DB for the entire test session."""
    db = tmp_path_factory.mktemp("data") / "test.db"
    os.environ["SQLITE_DB_PATH"] = f"sqlite:///{db}"
    yield
```

---

## 🟢 Low Priority — Polish & Portfolio Signal

### 17. The `Makefile` is Missing

A `Makefile` with common commands is a small but high-signal addition:
```makefile
.PHONY: dev worker test lint

dev:
	uvicorn src.main:app --reload

worker:
	python -m src.evaluation.evaluator

test:
	pytest tests/ -v

lint:
	ruff check src/ && mypy src/
```

This shows you know how to structure a project for a team, not just yourself.

---

### 18. `COST_MAPPING` in `metrics.py` Only Has Ollama Models

**File:** `src/api/middleware/metrics.py`, lines 13-16

```python
COST_MAPPING = {
    "phi3:latest": 0.0001,
    "llama3.2:3b": 0.0002
}
```

But the `.env.example` configures Groq and Gemini models. The cost accumulator will always produce `$0.00` for those models because they're not in this dict.

**Fix:**
```python
COST_MAPPING = {
    # Groq (free tier, but approximate if paid)
    "groq/llama-3.1-8b-instant": 0.00005,
    "groq/llama3-70b-8192": 0.00059,
    # Google Gemini
    "gemini/gemini-2.5-flash": 0.00015,
    "gemini/gemini-pro": 0.00125,
    # OpenAI
    "gpt-4o-mini": 0.00015,
    "gpt-4o": 0.005,
    # Anthropic
    "claude-3-haiku-20240307": 0.00025,
}
```

---

### 19. The `session_id` Field in `PredictRequest` Is Accepted But Never Used

**File:** `src/api/v1/endpoints.py`, line 19

```python
class PredictRequest(BaseModel):
    session_id: Optional[str] = None  # Accepted but silently discarded
```

Either use it (include in the trace payload, log it) or remove it. Dead API surface is confusing.

---

### 20. No `numpy` in `requirements.txt`

**File:** `requirements.txt`

`numpy` is used directly in `statistical.py` (`np.var`, `np.mean`, `np.sqrt`) but is not listed in `requirements.txt`. It works because `scipy` depends on numpy and installs it as a transitive dependency, but this is an implicit dependency that should be explicit.

**Fix:** Add `numpy>=1.26.0` to `requirements.txt`.

---

## Implementation Priority Roadmap

```
WEEK 1 — Critical Fixes (no new features, just correctness)
├── Fix test_llm_client.py argument order bug            [30 min]
├── Fix double Security import in auth.py                [5 min]
├── Replace list.pop(0) with collections.deque           [15 min]
└── Add threading.Lock to admin config update            [20 min]

WEEK 2 — High Impact Features (interview talking points)
├── Add promotion_signal event logging to evaluator      [1 hour]
├── Add /admin/experiment/summary endpoint               [2 hours]
├── Add ROUGE-L score to quality metrics                 [1 hour]
└── Add SQLite warmup to evaluation worker startup       [1 hour]

WEEK 3 — Architecture Completeness (production story)
├── Write Dockerfile for the API gateway                 [1 hour]
├── Add evaluation-worker service to docker-compose.yml  [30 min]
├── Use FastAPI lifespan instead of import-time side effects [1 hour]
└── Add config cache with TTL to router.py               [30 min]

WEEK 4 — Polish (looks professional)
├── Add conftest.py with test DB isolation               [1 hour]
├── Add Makefile                                         [30 min]
├── Fix COST_MAPPING for Groq/Gemini models              [20 min]
├── Add numpy to requirements.txt                        [5 min]
├── Add /admin/experiment/summary Grafana panels         [2 hours]
└── Remove or use session_id field                       [10 min]
```

---

## Interview Talking Points After Fixes

Once you implement the above, here is what you'll be able to say:

| Question | Your Answer |
|---|---|
| "What happens after you detect significance?" | "The evaluator logs a `promotion_signal` event to the audit table with the recommended action, p-value, Cohen's d, and sample size. You can also query `/admin/experiment/summary` to get the current statistical verdict at any time." |
| "How do you evaluate response quality, not just latency?" | "I compute ROUGE-L score between the control and challenger responses as a lexical overlap signal, plus token count differential and empty response detection. The architecture supports plugging in semantic similarity or LLM-as-a-judge scoring as the next layer." |
| "Is the system resilient to worker restarts?" | "Yes — on startup the worker pre-populates its sliding latency window from the last 1,000 records in SQLite, so it doesn't lose statistical state across restarts." |
| "How would you scale this?" | "The architecture is already horizontally scalable. The Redis queue is the central coordination point. The API gateway is stateless. The evaluation worker can run as multiple parallel consumers using Redis consumer groups instead of simple BRPOP." |
| "How does the hot reload work at scale?" | "The config is cached in-process with a 5-second TTL, so there's at most one file read per 5 seconds regardless of traffic volume. The cache is intentionally short to keep kill-switch latency low." |
