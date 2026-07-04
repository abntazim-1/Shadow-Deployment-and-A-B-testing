from fastapi import Request, Response
from prometheus_client import Histogram, Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from src.services.llm_client import cb
from src.core.config import settings

llm_request_latency_seconds = Histogram(
    'llm_request_latency_seconds',
    'Latency of LLM requests in seconds',
    ['model_name', 'routing_mode']
)

# Cost approximations ($ per 1k tokens) for simulation
COST_MAPPING = {
    "phi3:latest": 0.0001,
    "llama3.2:3b": 0.0002
}

llm_token_cost_dollars = Counter(
    'llm_token_cost_dollars',
    'Estimated expenditure if models were hosted',
    ['model_name']
)

llm_circuit_breaker_state = Gauge(
    'llm_circuit_breaker_state',
    'Circuit breaker state (0=closed, 1=open)',
    ['url']
)

async def metrics_middleware(request: Request, call_next):
    # Update gauges on every request
    llm_circuit_breaker_state.labels(url=str(settings.primary_llm_url)).set(
        1 if cb.is_open(str(settings.primary_llm_url)) else 0
    )
    if settings.shadow_enabled_global:
        llm_circuit_breaker_state.labels(url=str(settings.shadow_llm_url)).set(
            1 if cb.is_open(str(settings.shadow_llm_url)) else 0
        )
        
    return await call_next(request)

def get_metrics_endpoint() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
