from fastapi import APIRouter, Response, status
import sqlite3
import redis
from src.core.config import settings
from src.core.logging import logger

router = APIRouter(tags=["Health"])

@router.get("/healthz", status_code=status.HTTP_200_OK)
def liveness():
    """Liveness probe: returns 200 immediately if process is up."""
    return {"status": "up"}

@router.get("/readyz")
def readiness(response: Response):
    """Readiness probe: checks Redis and SQLite connections."""
    is_ready = True
    services = {"sqlite": "ok", "redis": "ok"}
    
    # Check SQLite
    try:
        db_path = settings.sqlite_db_path.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
    except Exception as e:
        is_ready = False
        services["sqlite"] = "down"
        logger.error("SQLite readiness check failed", error=str(e))
        
    # Check Redis
    try:
        redis_client = redis.Redis.from_url(settings.redis_url)
        redis_client.ping()
        redis_client.close()
    except Exception as e:
        is_ready = False
        services["redis"] = "down"
        logger.error("Redis readiness check failed", error=str(e))
        
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        
    return {"status": "ready" if is_ready else "not_ready", "services": services}
