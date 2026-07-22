from pydantic import BaseModel
from typing import Optional
import yaml
from pathlib import Path
from src.core.config import settings
from src.routing.strategies import is_challenger_assigned
from src.core.logging import logger
import time
import threading

import random

class RoutingDecision(BaseModel):
    routing_mode: str  # "control", "challenger", "shadow"
    primary_model_name: str
    shadow_enabled: bool = False
    shadow_model_name: Optional[str] = None
    shadow_sample_rate: float = 1.0

_config_cache: dict = {}
_cache_loaded_at: float = 0.0
CONFIG_TTL_SECONDS = 5.0
# Lock prevents two concurrent threads from both seeing an expired cache
# and both reading the YAML file simultaneously (classic TOCTOU race).
_cache_lock = threading.Lock()

def load_router_config() -> dict:
    global _config_cache, _cache_loaded_at
    # Fast path: cache is warm, return immediately without acquiring lock.
    if time.time() - _cache_loaded_at < CONFIG_TTL_SECONDS:
        return _config_cache
    
    with _cache_lock:
        # Double-check inside the lock: another thread may have refreshed
        # the cache while we were waiting to acquire the lock.
        if time.time() - _cache_loaded_at < CONFIG_TTL_SECONDS:
            return _config_cache
        
        config_path = Path("config/router_config.yaml")
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    _config_cache = yaml.safe_load(f) or {}
                    _cache_loaded_at = time.time()
                    return _config_cache
        except Exception as e:
            logger.error("Failed to load router config, using defaults", error=str(e))
            
        _cache_loaded_at = time.time()
        return _config_cache

def invalidate_cache():
    """Forces the config cache to be reloaded on the next check."""
    global _cache_loaded_at
    with _cache_lock:
        _cache_loaded_at = 0.0

def determine_route(user_id: str) -> RoutingDecision:
    # 1. Hot reload dynamic config
    dynamic_config = load_router_config()
    shadow_enabled_global = dynamic_config.get("shadow_enabled_global", settings.shadow_enabled_global)
    challenger_weight = dynamic_config.get("challenger_traffic_weight", settings.challenger_traffic_weight)
    sample_rate = float(dynamic_config.get("shadow_sample_rate", settings.shadow_sample_rate))

    # 2. Check A/B Assignment
    if is_challenger_assigned(user_id, settings.experiment_salt, challenger_weight):
        return RoutingDecision(
            routing_mode="challenger",
            primary_model_name=settings.shadow_model_name,
            shadow_sample_rate=sample_rate
        )
        
    # 3. Baseline / Shadow assignment
    should_shadow = bool(shadow_enabled_global) and (random.random() < sample_rate)
    return RoutingDecision(
        routing_mode="shadow" if shadow_enabled_global else "control",
        primary_model_name=settings.primary_model_name,
        shadow_enabled=should_shadow,
        shadow_model_name=settings.shadow_model_name if should_shadow else None,
        shadow_sample_rate=sample_rate
    )


