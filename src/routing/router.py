from pydantic import BaseModel
from typing import Optional
import yaml
from pathlib import Path
from src.core.config import settings
from src.routing.strategies import is_challenger_assigned
from src.core.logging import logger

class RoutingDecision(BaseModel):
    routing_mode: str  # "control", "challenger", "shadow"
    primary_model_name: str
    primary_url: str
    shadow_enabled: bool = False
    shadow_model_name: Optional[str] = None
    shadow_url: Optional[str] = None

def load_router_config() -> dict:
    config_path = Path("config/router_config.yaml")
    try:
        if config_path.exists():
            with open(config_path, "r") as f:
                return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error("Failed to load router config, using defaults", error=str(e))
    return {}

def determine_route(user_id: str) -> RoutingDecision:
    # 1. Hot reload dynamic config
    dynamic_config = load_router_config()
    shadow_enabled_global = dynamic_config.get("shadow_enabled_global", settings.shadow_enabled_global)
    challenger_weight = dynamic_config.get("challenger_traffic_weight", settings.challenger_traffic_weight)

    # 2. Check A/B Assignment
    if is_challenger_assigned(user_id, settings.experiment_salt, challenger_weight):
        return RoutingDecision(
            routing_mode="challenger",
            primary_model_name=settings.shadow_model_name,
            primary_url=str(settings.shadow_llm_url)
        )
        
    # 3. Baseline / Shadow assignment
    return RoutingDecision(
        routing_mode="shadow" if shadow_enabled_global else "control",
        primary_model_name=settings.primary_model_name,
        primary_url=str(settings.primary_llm_url),
        shadow_enabled=bool(shadow_enabled_global),
        shadow_model_name=settings.shadow_model_name if shadow_enabled_global else None,
        shadow_url=str(settings.shadow_llm_url) if shadow_enabled_global else None
    )
