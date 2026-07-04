from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
import uuid
import time
from typing import Optional

from src.routing.router import determine_route
from src.core.logging import logger
from src.services.llm_client import generate_completion
from src.services.queue_client import background_enqueue
from src.api.middleware.metrics import llm_request_latency_seconds, llm_token_cost_dollars, COST_MAPPING
from src.evaluation.metrics.quality import get_token_count

router = APIRouter()

class PredictRequest(BaseModel):
    user_id: str
    prompt: str
    session_id: Optional[str] = None

class PredictResponse(BaseModel):
    response: str
    routing_mode: str
    model_used: str

async def background_shadow_task(
    trace_id: str, 
    prompt: str,
    control_model: str,
    control_response: str, 
    control_latency_ms: float, 
    shadow_model: str, 
    shadow_url: str
):
    try:
        start_time = time.time()
        shadow_response = await generate_completion(shadow_model, shadow_url, prompt, is_shadow=True)
        shadow_latency_ms = (time.time() - start_time) * 1000
        
        logger.info("Shadow execution completed", 
                    trace_id=trace_id, 
                    shadow_model=shadow_model, 
                    latency_ms=shadow_latency_ms)
        
        # Telemetry
        llm_request_latency_seconds.labels(model_name=shadow_model, routing_mode="shadow").observe(shadow_latency_ms / 1000.0)
        tokens = get_token_count(prompt) + get_token_count(shadow_response)
        cost = (tokens / 1000.0) * COST_MAPPING.get(shadow_model, 0.0)
        llm_token_cost_dollars.labels(model_name=shadow_model).inc(cost)
        
        # Phase 4: Enqueue payload to Redis for evaluation worker
        payload = {
            "trace_id": trace_id,
            "prompt": prompt,
            "control_model": control_model,
            "control_response": control_response,
            "control_latency_ms": control_latency_ms,
            "challenger_model": shadow_model,
            "challenger_response": shadow_response,
            "challenger_latency_ms": shadow_latency_ms
        }
        background_enqueue(trace_id, payload)
        
    except Exception as e:
        logger.error("Shadow execution failed", trace_id=trace_id, error=str(e))

@router.post("/predict", response_model=PredictResponse)
async def predict(request: PredictRequest, background_tasks: BackgroundTasks):
    trace_id = str(uuid.uuid4())
    logger.info("Received predict request", user_id=request.user_id, trace_id=trace_id)
    
    route_decision = determine_route(request.user_id)
    logger.info("Routing decision", trace_id=trace_id, decision=route_decision.model_dump())
    
    start_time = time.time()
    response_text = await generate_completion(
        route_decision.primary_model_name, 
        route_decision.primary_url, 
        request.prompt,
        is_shadow=False
    )
    primary_latency_ms = (time.time() - start_time) * 1000
    
    # Telemetry
    llm_request_latency_seconds.labels(model_name=route_decision.primary_model_name, routing_mode=route_decision.routing_mode).observe(primary_latency_ms / 1000.0)
    tokens = get_token_count(request.prompt) + get_token_count(response_text)
    cost = (tokens / 1000.0) * COST_MAPPING.get(route_decision.primary_model_name, 0.0)
    llm_token_cost_dollars.labels(model_name=route_decision.primary_model_name).inc(cost)
    
    if route_decision.shadow_enabled and route_decision.shadow_model_name and route_decision.shadow_url:
        background_tasks.add_task(
            background_shadow_task,
            trace_id=trace_id,
            prompt=request.prompt,
            control_model=route_decision.primary_model_name,
            control_response=response_text,
            control_latency_ms=primary_latency_ms,
            shadow_model=route_decision.shadow_model_name,
            shadow_url=route_decision.shadow_url
        )
        
    return PredictResponse(
        response=response_text,
        routing_mode=route_decision.routing_mode,
        model_used=route_decision.primary_model_name
    )
