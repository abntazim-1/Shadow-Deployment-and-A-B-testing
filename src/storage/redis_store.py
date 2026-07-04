import redis.asyncio as redis
import json
from src.core.config import settings
from src.core.logging import logger

# Initialize async Redis client
redis_client = redis.from_url(settings.redis_url, decode_responses=True)

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
