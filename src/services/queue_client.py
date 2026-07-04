from src.storage.redis_store import enqueue_evaluation
from src.core.logging import logger
import asyncio

def background_enqueue(trace_id: str, payload: dict):
    """
    Fire-and-forget payload broker.
    Wraps the redis enqueue operation in an asyncio task to ensure 
    it never blocks the calling thread.
    """
    async def _enqueue():
        success = await enqueue_evaluation(payload)
        if success:
            logger.info("Successfully enqueued shadow payload", trace_id=trace_id)
        else:
            logger.error("Dropped shadow payload due to queue failure", trace_id=trace_id)
            
    asyncio.create_task(_enqueue())
