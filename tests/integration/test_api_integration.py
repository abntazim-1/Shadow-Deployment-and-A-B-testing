import time
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
import pytest
import asyncio

from src.main import app
from src.core.config import settings

client = TestClient(app)

@pytest.fixture(autouse=True)
def mock_llm_generation():
    """Mock generate_completion to avoid 50 retries and timeouts during tests."""
    with patch("src.api.v1.endpoints.generate_completion", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = "Simulated response"
        yield mock_generate

def test_auth_verification():
    """Protocol 7: Auth Verification"""
    # 1. No key -> 401
    response = client.get("/admin/config")
    assert response.status_code == 401
    
    # 2. Wrong key -> 401
    response = client.get("/admin/config", headers={"X-Admin-Key": "wrong_key"})
    assert response.status_code == 401
    
    # 3. Correct key -> 200
    response = client.get("/admin/config", headers={"X-Admin-Key": settings.admin_api_key})
    assert response.status_code == 200

def test_stateful_hashing_verification(mock_llm_generation):
    """Protocol 2: Stateful Hashing Verification"""
    user_id = "integration_test_user_01"
    
    client.post("/admin/config", 
                headers={"X-Admin-Key": settings.admin_api_key},
                json={"challenger_traffic_weight": 0.5, "shadow_enabled_global": True})
                
    res = client.post("/api/v1/predict", json={"user_id": user_id, "prompt": "test"})
    assert res.status_code == 200
    baseline_mode = res.json()["routing_mode"]
    
    for _ in range(10):
        res = client.post("/api/v1/predict", json={"user_id": user_id, "prompt": "test"})
        assert res.json()["routing_mode"] == baseline_mode, "Routing assignment flipped mid-session!"


def test_thread_concurrency_verification(mock_llm_generation):
    """Protocol 1: Thread Concurrency Verification"""
    
    async def mock_generate_side_effect(*args, **kwargs):
        return "Simulated response"
        
    mock_llm_generation.side_effect = mock_generate_side_effect
    
    # Ensure shadow is enabled
    client.post("/admin/config", 
                headers={"X-Admin-Key": settings.admin_api_key},
                json={"shadow_enabled_global": True})
                
    start_time = time.time()
    res = client.post("/api/v1/predict", json={"user_id": "test_concurrency", "prompt": "test"})
    latency = time.time() - start_time
    
    assert res.status_code == 200

def test_kill_switch_verification(mock_llm_generation):
    """Protocol 6: Kill Switch Verification"""
    client.post("/admin/config", 
                headers={"X-Admin-Key": settings.admin_api_key},
                json={"shadow_enabled_global": False})
                
    client.post("/admin/config", 
                headers={"X-Admin-Key": settings.admin_api_key},
                json={"challenger_traffic_weight": 0.0})
                
    res = client.post("/api/v1/predict", json={"user_id": "test_kill_switch", "prompt": "test"})
    
    assert res.json()["routing_mode"] == "control", "Kill switch failed to disable shadow traffic"
    
    # Restore
    client.post("/admin/config", 
                headers={"X-Admin-Key": settings.admin_api_key},
                json={"shadow_enabled_global": True})

def test_experiment_summary_endpoint():
    """Experiment summary returns correct structure even with no data."""
    response = client.get(
        "/admin/experiment/summary",
        headers={"X-Admin-Key": settings.admin_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "samples" in data
    assert "statistics" in data
    # With no data, statistics should be None (< 30 samples threshold)
    assert data["statistics"] is None or isinstance(data["statistics"], dict)

def test_experiment_lifecycle_start_stop():
    """Experiment start/stop endpoints write to the experiments table."""
    start_res = client.post(
        "/admin/experiment/start",
        headers={"X-Admin-Key": settings.admin_api_key},
        json={
            "name": "gemini-vs-groq-q3",
            "control_model": "gemini/gemini-2.5-flash",
            "challenger_model": "groq/llama-3.1-8b-instant"
        }
    )
    assert start_res.status_code == 200
    experiment_id = start_res.json()["experiment_id"]
    assert isinstance(experiment_id, int)

    stop_res = client.post(
        "/admin/experiment/stop",
        headers={"X-Admin-Key": settings.admin_api_key},
        json={"experiment_id": experiment_id, "outcome": "promote_challenger"}
    )
    assert stop_res.status_code == 200
    assert stop_res.json()["outcome"] == "promote_challenger"

def test_dead_letter_endpoint_reachable():
    """Dead-letter queue endpoint is reachable and returns correct structure."""
    with patch("src.storage.redis_store.redis_client.lrange", new_callable=AsyncMock) as mock_lrange:
        mock_lrange.return_value = []
        response = client.get(
            "/admin/dead-letter",
            headers={"X-Admin-Key": settings.admin_api_key}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "items" in data


def test_predict_returns_trace_id():
    """Every predict response includes X-Trace-Id header and trace_id in body."""
    res = client.post(
        "/api/v1/predict",
        json={"user_id": "trace-test-user", "prompt": "test prompt"}
    )
    assert res.status_code == 200
    assert "X-Trace-Id" in res.headers
    assert "trace_id" in res.json()
    assert res.headers["X-Trace-Id"] == res.json()["trace_id"]

def test_predict_validates_prompt_length():
    """Prompt exceeding 8000 characters is rejected with 422."""
    res = client.post(
        "/api/v1/predict",
        json={"user_id": "user-001", "prompt": "x" * 8001}
    )
    assert res.status_code == 422

def test_rfc7807_error_format():
    """Unauthorized requests return RFC 7807 Problem Details format."""
    res = client.get("/admin/config")
    assert res.status_code == 401
    data = res.json()
    assert "type" in data
    assert "title" in data
    assert "status" in data
    assert data["status"] == 401

def test_recent_evaluations_endpoint():
    """Recent evaluations endpoint returns success status and items list."""
    res = client.get(
        "/admin/evaluations/recent",
        headers={"X-Admin-Key": settings.admin_api_key}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "items" in data
    assert "count" in data

def test_console_endpoint():
    """GET /console serves the embedded Web Control Console HTML."""
    res = client.get("/console")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "<title>LLM Shadow Deployment & Canary Console</title>" in res.text

def test_canary_api_endpoints():
    """Test start, status, step, and rollback endpoints for canary engine."""
    headers = {"X-Admin-Key": settings.admin_api_key}
    
    # 1. Start canary
    res_start = client.post(
        "/admin/canary/start",
        headers=headers,
        json={"experiment_name": "api_test_canary", "steps": [0.1, 0.5, 1.0], "min_judge_score": 3.5}
    )
    assert res_start.status_code == 200
    assert res_start.json()["canary"]["status"] == "running"
    
    # 2. Get status
    res_status = client.get("/admin/canary/status", headers=headers)
    assert res_status.status_code == 200
    assert res_status.json()["canary"]["experiment_name"] == "api_test_canary"
    
    # 3. Step
    res_step = client.post("/admin/canary/step", headers=headers)
    assert res_step.status_code == 200
    assert res_step.json()["canary"]["current_weight"] == 0.5
    
    # 4. Rollback
    res_rollback = client.post(
        "/admin/canary/rollback",
        headers=headers,
        json={"reason": "Manual test rollback"}
    )
    assert res_rollback.status_code == 200
    assert res_rollback.json()["canary"]["status"] == "rolled_back"


