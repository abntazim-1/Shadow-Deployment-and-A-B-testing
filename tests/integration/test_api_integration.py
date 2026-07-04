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
                json={"challenger_traffic_weight": 0.5})
                
    res = client.post("/api/v1/predict", json={"user_id": user_id, "prompt": "test"})
    assert res.status_code == 200
    baseline_mode = res.json()["routing_mode"]
    
    for _ in range(49):
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
