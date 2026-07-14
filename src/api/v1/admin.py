from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
import yaml
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from src.api.middleware.auth import verify_admin_api_key
from src.core.logging import logger
from src.storage.sqlite_store import log_experiment_event, get_connection
from src.evaluation.metrics.statistical import calculate_welchs_ttest

router = APIRouter(tags=["Admin"], dependencies=[Depends(verify_admin_api_key)])

CONFIG_PATH = Path("config/router_config.yaml")

class RouterConfig(BaseModel):
    shadow_enabled_global: Optional[bool] = None
    challenger_traffic_weight: Optional[float] = None

class ExperimentStart(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    control_model: str
    challenger_model: str

class ExperimentStop(BaseModel):
    experiment_id: int
    outcome: str = Field(..., pattern="^(promote_challenger|retain_control|inconclusive)$")

@router.get("/config")
def get_config():
    """Retrieve the current router configuration."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    return {}

_config_lock = threading.Lock()

@router.post("/config")
def update_config(config_update: RouterConfig):
    """Update the router configuration and log the event."""
    with _config_lock:
        current_config = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                current_config = yaml.safe_load(f) or {}

        # Apply updates
        if config_update.shadow_enabled_global is not None:
            current_config["shadow_enabled_global"] = config_update.shadow_enabled_global
        if config_update.challenger_traffic_weight is not None:
            current_config["challenger_traffic_weight"] = config_update.challenger_traffic_weight

        with open(CONFIG_PATH, "w") as f:
            yaml.safe_dump(current_config, f)
            
        from src.routing.router import invalidate_cache
        invalidate_cache()

    # Log the audit event
    log_experiment_event(
        event_type="config_update",
        details=current_config
    )
    logger.info("Admin config updated", config=current_config)

    return {"status": "success", "config": current_config}

@router.get("/experiment/summary")
def get_experiment_summary():
    """Returns the current statistical state of the running experiment."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT control_latency_ms, challenger_latency_ms FROM evaluations ORDER BY id DESC LIMIT 1000"
        ).fetchall()

        control_latencies = [row[0] for row in reversed(rows)]
        challenger_latencies = [row[1] for row in reversed(rows)]

        stats_result = None
        if len(control_latencies) >= 30:  # MIN_STATISTICAL_SAMPLES
            stats_result = calculate_welchs_ttest(control_latencies, challenger_latencies)

        return {
            "status": "success",
            "samples": len(control_latencies),
            "statistics": stats_result
        }
    except Exception as e:
        logger.error("Failed to get experiment summary", error=str(e))
        return {"status": "error", "message": "Failed to calculate summary"}
    finally:
        conn.close()

@router.post("/experiment/start")
def start_experiment(body: ExperimentStart):
    """
    Register the start of a new A/B experiment.
    Records control model, challenger model, and start time in the
    experiments table so experiment history is durable and queryable.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO experiments (name, control_model, challenger_model) VALUES (?, ?, ?)",
            (body.name, body.control_model, body.challenger_model)
        )
        conn.commit()
        experiment_id = cursor.lastrowid
        log_experiment_event("experiment_started", {
            "experiment_id": experiment_id,
            "name": body.name,
            "control_model": body.control_model,
            "challenger_model": body.challenger_model,
        })
        logger.info("Experiment started", experiment_id=experiment_id, name=body.name)
        return {"status": "success", "experiment_id": experiment_id}
    finally:
        conn.close()

@router.post("/experiment/stop")
def stop_experiment(body: ExperimentStop):
    """
    Mark an experiment as complete with its outcome.
    outcome must be one of: promote_challenger | retain_control | inconclusive
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE experiments SET status='completed', ended_at=?, outcome=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), body.outcome, body.experiment_id)
        )
        conn.commit()
        log_experiment_event("experiment_stopped", {
            "experiment_id": body.experiment_id,
            "outcome": body.outcome,
        })
        logger.info("Experiment stopped", experiment_id=body.experiment_id, outcome=body.outcome)
        return {"status": "success", "outcome": body.outcome}
    finally:
        conn.close()

@router.get("/dead-letter")
async def get_dead_letter_queue():
    """
    Returns the most recent 50 failed evaluation payloads.
    Use this to inspect what went wrong and re-enqueue for replay.
    """
    from src.storage.redis_store import redis_client
    import json
    try:
        raw = await redis_client.lrange("llm_shadow_queue:dead_letter", 0, 49)
        items = [json.loads(r) for r in raw]
        return {"status": "success", "count": len(items), "items": items}
    except Exception as e:
        logger.error("Failed to fetch dead-letter queue", error=str(e))
        return {"status": "error", "message": str(e)}
