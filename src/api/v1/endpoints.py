from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uuid
import time
import structlog
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.routing.router import determine_route
from src.core.logging import logger
from src.services.llm_client import generate_completion
from src.services.queue_client import background_enqueue
from src.api.middleware.metrics import llm_request_latency_seconds, llm_token_cost_dollars, COST_MAPPING
from src.evaluation.metrics.quality import get_token_count
from src.security.pii_scrubber import sanitize_pii


limiter = Limiter(key_func=get_remote_address)
router = APIRouter()

class PredictRequest(BaseModel):
    # Field validation: prevents empty strings and runaway prompts that would
    # burn the entire LLM API quota in a single request.
    user_id: str = Field(..., min_length=1, max_length=256)
    prompt: str = Field(..., min_length=1, max_length=8000)

class PredictResponse(BaseModel):
    response: str
    routing_mode: str
    model_used: str
    trace_id: str

@router.post("/predict", response_model=PredictResponse)
@limiter.limit("60/minute")
async def predict(request: Request, body: PredictRequest):
    trace_id = str(uuid.uuid4())
    
    # Bind trace_id into structlog context — all subsequent log calls
    # in this request automatically include it without passing it manually.
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(trace_id=trace_id, user_id=body.user_id)
    logger.info("Received predict request")
    
    route_decision = determine_route(body.user_id)
    logger.info("Routing decision", decision=route_decision.model_dump())
    
    start_time = time.time()
    response_text = await generate_completion(
        route_decision.primary_model_name, 
        body.prompt,
        is_shadow=False
    )
    primary_latency_ms = (time.time() - start_time) * 1000
    
    # Telemetry
    llm_request_latency_seconds.labels(model_name=route_decision.primary_model_name, routing_mode=route_decision.routing_mode).observe(primary_latency_ms / 1000.0)
    tokens = get_token_count(body.prompt) + get_token_count(response_text)
    cost = (tokens / 1000.0) * COST_MAPPING.get(route_decision.primary_model_name, 0.0)
    llm_token_cost_dollars.labels(model_name=route_decision.primary_model_name).inc(cost)
    
    if route_decision.shadow_enabled and route_decision.shadow_model_name:
        # Decoupled design & PII Sanitization: sanitize prompt for 3rd-party challenger model payload
        sanitized_prompt = sanitize_pii(body.prompt)
        payload = {
            "trace_id": trace_id,
            "user_id": body.user_id,
            "prompt": sanitized_prompt,
            "control_model": route_decision.primary_model_name,
            "control_response": response_text,
            "control_latency_ms": primary_latency_ms,
            "challenger_model": route_decision.shadow_model_name
        }
        background_enqueue(trace_id, payload)

    
    result = PredictResponse(
        response=response_text,
        routing_mode=route_decision.routing_mode,
        model_used=route_decision.primary_model_name,
        trace_id=trace_id
    )
    # X-Trace-Id lets clients correlate their request to server-side logs.
    return JSONResponse(content=result.model_dump(), headers={"X-Trace-Id": trace_id})

