from fastapi import APIRouter, Depends
from pydantic import BaseModel
import yaml
from pathlib import Path
from typing import Optional
from src.api.middleware.auth import verify_admin_api_key
from src.core.logging import logger
from src.storage.sqlite_store import log_experiment_event

router = APIRouter(tags=["Admin"], dependencies=[Depends(verify_admin_api_key)])

CONFIG_PATH = Path("config/router_config.yaml")

class RouterConfig(BaseModel):
    shadow_enabled_global: Optional[bool] = None
    challenger_traffic_weight: Optional[float] = None

@router.get("/config")
def get_config():
    """Retrieve the current router configuration."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    return {}

@router.post("/config")
def update_config(config_update: RouterConfig):
    """Update the router configuration and log the event."""
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
        
    # Log the audit event
    log_experiment_event(
        event_type="config_update",
        details=current_config
    )
    logger.info("Admin config updated", config=current_config)
    
    return {"status": "success", "config": current_config}
