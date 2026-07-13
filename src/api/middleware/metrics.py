from fastapi import Request, Response
from prometheus_client import Histogram, Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from src.services.llm_client import cb
from src.core.config import settings
from src.storage.redis_store import get_queue_depth

# LLM-appropriate histogram buckets.
# Default Prometheus buckets (.005s – 10s) are designed for web APIs.
# LLM calls take 100ms – 60s, so without custom buckets the entire
# distribution falls into the +Inf bucket and percentile queries are useless.
LLM_LATENCY_BUCKETS = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0]

llm_request_latency_seconds = Histogram(
    'llm_request_latency_seconds',
    'Latency of LLM requests in seconds',
    ['model_name', 'routing_mode'],
    buckets=LLM_LATENCY_BUCKETS
)

# Cost approximations ($ per 1k tokens) for simulation
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

llm_token_cost_dollars = Counter(
    'llm_token_cost_dollars',
    'Estimated expenditure if models were hosted',
    ['model_name']
)

llm_circuit_breaker_state = Gauge(
    'llm_circuit_breaker_state',
    'Circuit breaker state (0=closed, 1=open)',
    ['model_name']
)

# Queue depth gauge: if this grows, the evaluation worker is saturated.
# Alert on this metric before Redis memory is exhausted.
redis_evaluation_queue_depth = Gauge(
    'redis_evaluation_queue_depth',
    'Number of pending items in the shadow evaluation queue'
)

async def metrics_middleware(request: Request, call_next):
    # Update gauges on every request
    llm_circuit_breaker_state.labels(model_name=settings.primary_model_name).set(
        1 if cb.is_open(settings.primary_model_name) else 0
    )
    if settings.shadow_enabled_global:
        llm_circuit_breaker_state.labels(model_name=settings.shadow_model_name).set(
            1 if cb.is_open(settings.shadow_model_name) else 0
        )
    
    # Update queue depth — non-blocking async call
    depth = await get_queue_depth()
    if depth >= 0:  # -1 means Redis error, don't update
        redis_evaluation_queue_depth.set(depth)
        
    return await call_next(request)

def get_metrics_endpoint() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
