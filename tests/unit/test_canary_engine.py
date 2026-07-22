import pytest
from src.routing.canary_engine import canary_engine, CanaryState

def test_canary_rollout_lifecycle():
    # 1. Start canary
    state = canary_engine.start_canary(
        experiment_name="test_canary_01",
        steps=[0.1, 0.5, 1.0],
        min_judge_score=3.5
    )
    assert state.status == "running"
    assert state.current_weight == 0.1
    assert state.current_step_index == 0

    # 2. Advance step to 0.5
    state_step1 = canary_engine.advance_step()
    assert state_step1.current_weight == 0.5
    assert state_step1.current_step_index == 1

    # 3. Advance step to 1.0
    state_step2 = canary_engine.advance_step()
    assert state_step2.current_weight == 1.0
    assert state_step2.current_step_index == 2

    # 4. Advance step past last step -> completed
    state_completed = canary_engine.advance_step()
    assert state_completed.status == "completed"
    assert state_completed.current_weight == 1.0


def test_canary_manual_rollback():
    canary_engine.start_canary(experiment_name="test_canary_rollback")
    rollback_state = canary_engine.trigger_rollback(reason="Safety violation")
    assert rollback_state.status == "rolled_back"
    assert rollback_state.current_weight == 0.0
    assert rollback_state.rollback_reason == "Safety violation"
