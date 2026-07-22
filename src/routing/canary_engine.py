import threading
import time
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from pathlib import Path
from src.core.logging import logger
from src.storage.sqlite_store import log_experiment_event, get_connection
from src.routing.router import load_router_config, invalidate_cache
import yaml

CONFIG_PATH = Path("config/router_config.yaml")


class CanaryState(BaseModel):
    status: str = "idle"  # idle, running, completed, rolled_back
    experiment_name: str = ""
    current_step_index: int = 0
    steps: List[float] = [0.05, 0.20, 0.50, 1.0]
    min_judge_score_guardrail: float = 3.5
    sample_count: int = 0
    current_weight: float = 0.0
    last_updated: float = 0.0
    rollback_reason: Optional[str] = None

class CanaryEngine:
    """
    Automated Canary Traffic Shifting & Auto-Rollback Engine.
    Progressively shifts production traffic to a challenger model while
    continuously evaluating guardrail metrics (LLM judge score).
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._state = CanaryState()

    def get_state(self) -> CanaryState:
        with self._lock:
            return self._state.model_copy()

    def _update_router_weight(self, weight: float):
        """Helper to update router_config.yaml and invalidate cache."""
        current_config = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                current_config = yaml.safe_load(f) or {}

        current_config["challenger_traffic_weight"] = weight
        current_config["shadow_enabled_global"] = True

        with open(CONFIG_PATH, "w") as f:
            yaml.safe_dump(current_config, f)
            
        invalidate_cache()

    def start_canary(
        self, 
        experiment_name: str, 
        steps: Optional[List[float]] = None, 
        min_judge_score: float = 3.5
    ) -> CanaryState:
        with self._lock:
            if steps is None:
                steps = [0.05, 0.20, 0.50, 1.0]
            
            initial_weight = steps[0]
            self._state = CanaryState(
                status="running",
                experiment_name=experiment_name,
                current_step_index=0,
                steps=steps,
                min_judge_score_guardrail=min_judge_score,
                sample_count=0,
                current_weight=initial_weight,
                last_updated=time.time(),
                rollback_reason=None
            )
            self._update_router_weight(initial_weight)

        log_experiment_event("canary_started", self._state.model_dump())
        logger.info("Canary rollout started", experiment=experiment_name, initial_weight=initial_weight)
        return self.get_state()

    def trigger_rollback(self, reason: str) -> CanaryState:
        with self._lock:
            self._state.status = "rolled_back"
            self._state.current_weight = 0.0
            self._state.rollback_reason = reason
            self._state.last_updated = time.time()
            self._update_router_weight(0.0)

        log_experiment_event("canary_rollback", {"reason": reason, "experiment": self._state.experiment_name})
        logger.warning("Canary rollback triggered!", reason=reason)
        return self.get_state()

    def advance_step(self) -> CanaryState:
        with self._lock:
            if self._state.status != "running":
                return self._state.model_copy()

            next_index = self._state.current_step_index + 1
            if next_index >= len(self._state.steps):
                self._state.status = "completed"
                self._state.current_weight = 1.0
                self._state.last_updated = time.time()
                self._update_router_weight(1.0)
                log_experiment_event("canary_completed", {"experiment": self._state.experiment_name})
                logger.info("Canary rollout completed successfully! Challenger at 100%.", experiment=self._state.experiment_name)
            else:
                next_weight = self._state.steps[next_index]
                self._state.current_step_index = next_index
                self._state.current_weight = next_weight
                self._state.last_updated = time.time()
                self._update_router_weight(next_weight)
                log_experiment_event("canary_stepped", {
                    "experiment": self._state.experiment_name,
                    "step_index": next_index,
                    "weight": next_weight
                })
                logger.info("Canary rollout stepped up", step=next_index, weight=next_weight)

        return self.get_state()

    def evaluate_guardrails(self) -> CanaryState:
        """
        Query recent evaluations to evaluate whether guardrails pass.
        Triggers rollback if average judge score drops below threshold.
        """
        state = self.get_state()
        if state.status != "running":
            return state

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT judge_score FROM evaluations ORDER BY id DESC LIMIT 20"
            ).fetchall()
            if len(rows) >= 5:
                scores = [r[0] for r in rows if r[0] is not None and r[0] > 0]
                if scores:
                    avg_score = sum(scores) / len(scores)
                    with self._lock:
                        self._state.sample_count = len(scores)

                    if avg_score < state.min_judge_score_guardrail:
                        reason = f"Average judge score ({avg_score:.2f}) dropped below guardrail threshold ({state.min_judge_score_guardrail})."
                        return self.trigger_rollback(reason)
        except Exception as e:
            logger.error("Failed to evaluate canary guardrails", error=str(e))
        finally:
            conn.close()

        return self.get_state()

# Global singleton canary engine
canary_engine = CanaryEngine()
