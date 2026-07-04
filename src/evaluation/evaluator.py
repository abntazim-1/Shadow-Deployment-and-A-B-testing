import asyncio
from typing import List, Dict, Any
from src.core.logging import logger
from src.storage.redis_store import dequeue_evaluation
from src.storage.sqlite_store import save_evaluation
from src.evaluation.metrics.quality import calculate_quality_metrics
from src.evaluation.metrics.statistical import calculate_welchs_ttest

# In-memory arrays to hold sliding windows of latencies for statistical calculation
control_latencies: List[float] = []
challenger_latencies: List[float] = []
MIN_STATISTICAL_SAMPLES = 30

async def process_payload(payload: Dict[str, Any]):
    trace_id = payload.get("trace_id")
    try:
        # 1. Quality Metrics
        quality = calculate_quality_metrics(
            payload.get("prompt", ""),
            payload.get("control_response", ""),
            payload.get("challenger_response", "")
        )
        
        # 2. Append to statistical window (keep last 1000 to prevent unbounded memory growth)
        c_lat = float(payload.get("control_latency_ms", 0))
        sh_lat = float(payload.get("challenger_latency_ms", 0))
        
        control_latencies.append(c_lat)
        challenger_latencies.append(sh_lat)
        
        if len(control_latencies) > 1000:
            control_latencies.pop(0)
            challenger_latencies.pop(0)
            
        # 3. Calculate statistics if we have enough samples
        stats_result = None
        if len(control_latencies) >= MIN_STATISTICAL_SAMPLES:
            stats_result = calculate_welchs_ttest(control_latencies, challenger_latencies)
            if stats_result.get("significant"):
                logger.warning("Statistically significant latency difference detected!", 
                               p_value=stats_result.get("p_value"), 
                               cohens_d=stats_result.get("cohens_d"))
        
        # 4. Save durable record to SQLite
        save_evaluation(payload)
        
        logger.info("Successfully evaluated shadow payload", 
                    trace_id=trace_id, 
                    quality_metrics=quality, 
                    statistics=stats_result)
                    
    except Exception as e:
        logger.error("Error evaluating payload", trace_id=trace_id, error=str(e))

async def run_worker():
    logger.info("Evaluation worker started. Listening to Redis queue...")
    while True:
        try:
            payload = await dequeue_evaluation()
            if payload:
                await process_payload(payload)
            else:
                # Optional small sleep if timeout didn't block
                pass 
        except asyncio.CancelledError:
            logger.info("Worker gracefully shutting down.")
            break
        except Exception as e:
            logger.error("Worker encountered an error", error=str(e))
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(run_worker())
