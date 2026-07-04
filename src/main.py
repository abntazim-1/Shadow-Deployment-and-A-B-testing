from fastapi import FastAPI
from src.api.v1.endpoints import router as v1_router
from src.api.middleware.metrics import metrics_middleware, get_metrics_endpoint
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI(title="Shadow Deployment & A/B Testing Framework")

app.add_middleware(BaseHTTPMiddleware, dispatch=metrics_middleware)

app.include_router(v1_router, prefix="/api/v1")

@app.get("/metrics")
def metrics():
    return get_metrics_endpoint()

@app.get("/")
def root():
    return {"status": "up and running"}
