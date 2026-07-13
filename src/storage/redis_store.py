import redis.asyncio as redis
import json
from datetime import datetime, timezone
from src.core.config import settings
from src.core.logging import logger

# Production-grade Redis client:
# - max_connections=20: pool so multiple coroutines share connections
# - socket_connect_timeout: fail fast if Redis is unreachable (don't hang)
# - socket_timeout: fail fast on stalled read/write
# - retry_on_timeout=True: auto-retry on transient network blips
redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    max_connections=20,
    socket_connect_timeout=5,
    socket_timeout=5,
    retry_on_timeout=True,
)

async def enqueue_evaluation(payload: dict) -> bool:
    """Push evaluation payload to the Redis queue."""
    try:
        await redis_client.lpush("llm_shadow_queue", json.dumps(payload))
        return True
    except Exception as e:
        logger.error("Failed to enqueue evaluation", error=str(e))
        return False
        
async def dequeue_evaluation() -> dict | None:
    """Pop evaluation payload from the Redis queue."""
    try:
        result = await redis_client.brpop("llm_shadow_queue", timeout=1)
        if result:
            return json.loads(result[1])
        return None
    except Exception as e:
        logger.error("Failed to dequeue evaluation", error=str(e))
        return None

async def push_to_dead_letter(payload: dict, error: str):
    """
    Push a failed evaluation payload to the dead-letter queue.
    This ensures no evaluation is ever silently dropped — failures
    are inspectable and replayable via /admin/dead-letter.
    """
    record = {
        "original_payload": payload,
        "error": error,
        "failed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await redis_client.lpush("llm_shadow_queue:dead_letter", json.dumps(record))
        logger.warning("Pushed failed evaluation to dead-letter queue",
                       trace_id=payload.get("trace_id"), error=error)
    except Exception as e:
        logger.error("CRITICAL: Failed to push to dead-letter queue", error=str(e))

async def get_queue_depth() -> int:
    """Returns current number of items in the evaluation queue (for Prometheus)."""
    try:
        return await redis_client.llen("llm_shadow_queue")
    except Exception:
        return -1
