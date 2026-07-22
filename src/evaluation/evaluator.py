import time
import asyncio
from typing import Dict, Any
from collections import deque
from src.core.logging import logger
from src.storage.redis_store import dequeue_evaluation, push_to_dead_letter
from src.storage.sqlite_store import save_evaluation, log_experiment_event, get_connection
from src.evaluation.metrics.quality import evaluate_semantic_quality_with_judge, get_token_count
from src.evaluation.metrics.statistical import calculate_welchs_ttest
from src.services.llm_client import generate_completion
from src.api.middleware.metrics import llm_request_latency_seconds, llm_token_cost_dollars, COST_MAPPING

# In-memory arrays to hold sliding windows of latencies for statistical calculation
control_latencies: deque = deque(maxlen=1000)
challenger_latencies: deque = deque(maxlen=1000)
MIN_STATISTICAL_SAMPLES = 30

async def process_payload(payload: Dict[str, Any]):
    trace_id = payload.get("trace_id")
    try:
        prompt = payload.get("prompt", "")
        control_response = payload.get("control_response", "")
        control_model = payload.get("control_model", "")
        challenger_model = payload.get("challenger_model", "")
        
        # 1. Execute Shadow LLM call if not pre-computed by gateway (Decoupled Architecture)
        if "challenger_response" not in payload or payload.get("challenger_response") is None:
            start_time = time.time()
            challenger_response = await generate_completion(challenger_model, prompt, is_shadow=True)
            challenger_latency_ms = (time.time() - start_time) * 1000.0
            
            payload["challenger_response"] = challenger_response
            payload["challenger_latency_ms"] = challenger_latency_ms
            
            # Telemetry for worker-executed shadow completion
            llm_request_latency_seconds.labels(model_name=challenger_model, routing_mode="shadow").observe(challenger_latency_ms / 1000.0)
            tokens = get_token_count(prompt) + get_token_count(challenger_response)
            cost = (tokens / 1000.0) * COST_MAPPING.get(challenger_model, 0.0)
            llm_token_cost_dollars.labels(model_name=challenger_model).inc(cost)
        else:
            challenger_response = payload.get("challenger_response", "")
            challenger_latency_ms = float(payload.get("challenger_latency_ms", 0.0))

        # 2. Semantic & LLM-as-a-Judge Evaluation
        quality_results = await evaluate_semantic_quality_with_judge(
            prompt=prompt,
            control_resp=control_response,
            challenger_resp=challenger_response
        )
        
        payload["judge_score"] = quality_results.get("judge_score", 0.0)
        payload["semantic_equivalence"] = quality_results.get("semantic_equivalence", 0.0)
        payload["judge_reasoning"] = quality_results.get("judge_reasoning", "")
        
        # 3. Append to statistical window (keep last 1000 to prevent unbounded memory growth)
        c_lat = float(payload.get("control_latency_ms", 0))
        control_latencies.append(c_lat)
        challenger_latencies.append(challenger_latency_ms)
        
        # 4. Calculate statistics if we have enough samples
        stats_result = None
        if len(control_latencies) >= MIN_STATISTICAL_SAMPLES:
            stats_result = calculate_welchs_ttest(control_latencies, challenger_latencies)
            if stats_result and stats_result.get("significant"):
                logger.warning("Statistically significant latency difference detected!", 
                               p_value=stats_result.get("p_value"), 
                               cohens_d=stats_result.get("cohens_d"))
                               
                if abs(stats_result.get("cohens_d", 0)) > 0.5:
                    winner = "control" if stats_result["cohens_d"] > 0 else "challenger"
                    log_experiment_event("promotion_signal", {
                        "recommended_action": f"promote_{winner}",
                        "p_value": stats_result["p_value"],
                        "cohens_d": stats_result["cohens_d"],
                        "sample_size": len(control_latencies)
                    })
        
        # 5. Save durable record to SQLite
        save_evaluation(payload)
        
        logger.info("Successfully evaluated shadow payload", 
                    trace_id=trace_id, 
                    quality_metrics=quality_results, 
                    statistics=stats_result)
                    
    except Exception as e:
        logger.error("Error evaluating payload", trace_id=trace_id, error=str(e))
        # Dead-letter: push failed payload so it can be inspected and replayed.
        await push_to_dead_letter(payload, str(e))


async def warmup_from_sqlite():
    """Pre-populate latency windows from durable SQLite history on startup."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT control_latency_ms, challenger_latency_ms FROM evaluations ORDER BY id DESC LIMIT 1000"
        ).fetchall()
        for row in reversed(rows):  # oldest first
            control_latencies.append(row[0])
            challenger_latencies.append(row[1])
        logger.info("Warmed up latency windows from SQLite", samples=len(control_latencies))
    except Exception as e:
        logger.error("Failed to warmup from SQLite", error=str(e))
    finally:
        conn.close()

# Bounds the number of concurrent evaluation coroutines.
# Without this, a queue burst could spawn thousands of tasks and OOM the process.
_WORKER_CONCURRENCY = 10
_sem = asyncio.Semaphore(_WORKER_CONCURRENCY)

async def run_worker():
    await warmup_from_sqlite()
    logger.info("Evaluation worker started. Listening to Redis queue...",
                concurrency=_WORKER_CONCURRENCY)
    active_tasks: set = set()
    while True:
        try:
            payload = await dequeue_evaluation()
            if payload:
                # Acquire semaphore before spawning — blocks if 10 tasks already running.
                # This provides backpressure: the queue drains at most 10x parallel rate.
                await _sem.acquire()
                async def _run(p):
                    try:
                        await process_payload(p)
                    finally:
                        _sem.release()
                task = asyncio.create_task(_run(payload))
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)
        except asyncio.CancelledError:
            logger.info("Worker shutting down. Draining active tasks...",
                        remaining=len(active_tasks))
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)
            break
        except Exception as e:
            logger.error("Worker encountered an error", error=str(e))
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(run_worker())
