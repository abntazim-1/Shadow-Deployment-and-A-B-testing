import pytest
import asyncio
from unittest.mock import patch
from src.services.llm_client import generate_completion, cb

def test_llm_circuit_breaker_and_fallback():
    async def run_test():
        model_name = "test_model"
        prompt = "Hello world"

        # reset cb for test safety
        cb._failures[model_name] = 0
        cb._last_failure_time[model_name] = 0

        # trigger failures to cross the threshold (5)
        with patch('litellm.acompletion', side_effect=Exception("Simulated error")):
            for _ in range(5):
                res = await generate_completion(model_name, prompt)
                assert "[Synthetic Fallback]" in res
                
            # Circuit breaker should now be OPEN
            assert cb.is_open(model_name) == True
            
            # Next request fast-fails to fallback without retrying
            res = await generate_completion(model_name, prompt)
            assert "[Synthetic Fallback]" in res

    asyncio.run(run_test())
