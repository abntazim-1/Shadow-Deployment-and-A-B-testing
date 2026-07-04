import pytest
import asyncio
from src.services.llm_client import generate_completion, cb

def test_llm_circuit_breaker_and_fallback():
    async def run_test():
        # A URL that is guaranteed to fail immediately
        bad_url = "http://localhost:9999/api/generate"
        model_name = "test_model"
        prompt = "Hello world"

        # reset cb for test safety
        cb._failures[bad_url] = 0
        cb._last_failure_time[bad_url] = 0

        # trigger failures to cross the threshold (5)
        for _ in range(5):
            res = await generate_completion(model_name, bad_url, prompt)
            assert "[Synthetic Fallback]" in res
            
        # Circuit breaker should now be OPEN
        assert cb.is_open(bad_url) == True
        
        # Next request fast-fails to fallback without retrying
        res = await generate_completion(model_name, bad_url, prompt)
        assert "[Synthetic Fallback]" in res

    asyncio.run(run_test())
