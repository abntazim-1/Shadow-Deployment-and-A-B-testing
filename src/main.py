from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException
from contextlib import asynccontextmanager
from src.storage.sqlite_store import init_db
from src.evaluation.evaluator import run_worker
from src.core.logging import setup_logging, logger
import asyncio
from src.api.v1.endpoints import router as v1_router
from src.api.middleware.metrics import metrics_middleware, get_metrics_endpoint
from starlette.middleware.base import BaseHTTPMiddleware
from src.api.v1.health import router as health_router
from src.api.v1.admin import router as admin_router
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from src.api.v1.endpoints import limiter

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run logging setup here (not at import time) so tests can configure
    # log levels independently without stdout pollution.
    setup_logging()
    # Startup
    init_db()
    worker_task = asyncio.create_task(run_worker())
    yield
    # Graceful shutdown: give the worker up to 10 seconds to finish
    # any in-flight evaluations before the process exits.
    # Without this, a SIGTERM mid-write loses that SQLite record.
    worker_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(worker_task), timeout=10.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    logger.info("Shutdown complete.")

app = FastAPI(title="Shadow Deployment & A/B Testing Framework", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.exception_handler(HTTPException)
async def rfc7807_exception_handler(request: Request, exc: HTTPException):
    """
    RFC 7807 Problem Details for HTTP APIs.
    Replaces FastAPI's default {"detail": "..."} with a structured format
    that every enterprise API client, SDK, and monitoring tool understands.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": f"https://httpstatuses.com/{exc.status_code}",
            "title": exc.detail,
            "status": exc.status_code,
            "detail": exc.detail,
            "instance": str(request.url.path),
        },
        headers={"Content-Type": "application/problem+json"},
    )

app.add_middleware(BaseHTTPMiddleware, dispatch=metrics_middleware)

app.include_router(v1_router, prefix="/api/v1")
app.include_router(health_router, prefix="")
app.include_router(admin_router, prefix="/admin")

@app.get("/metrics")
def metrics():
    return get_metrics_endpoint()

@app.get("/")
def root():
    return {"status": "up and running"}
