import asyncio
import time
import random
import litellm
from typing import Dict
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from src.core.logging import logger

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, cooldown_secs: int = 60):
        self.failure_threshold = failure_threshold
        self.cooldown_secs = cooldown_secs
        
        # State tracking per model name
        self._failures: Dict[str, int] = {}
        self._last_failure_time: Dict[str, float] = {}

    def is_open(self, model: str) -> bool:
        failures = self._failures.get(model, 0)
        if failures >= self.failure_threshold:
            last_fail = self._last_failure_time.get(model, 0)
            if time.time() - last_fail < self.cooldown_secs:
                return True
            else:
                # Cooldown expired, half-open state
                self._failures[model] = self.failure_threshold - 1
                return False
        return False

    def record_failure(self, model: str):
        self._failures[model] = self._failures.get(model, 0) + 1
        self._last_failure_time[model] = time.time()
        logger.warning("Circuit breaker recorded failure", model=model, failures=self._failures[model])

    def record_success(self, model: str):
        if model in self._failures:
            self._failures[model] = 0
            self._last_failure_time[model] = 0

# Singleton instance
cb = CircuitBreaker()

async def synthetic_fallback_response(prompt: str, model_name: str) -> str:
    """
    Compute-free fallback simulation using mathematical exponential random distributions
    to simulate network lag without drawing local system memory resources.
    """
    logger.info("Engaging synthetic fallback streamer", model=model_name)
    # Simulate TTFT (Time To First Token) with lambda = 2.0 (mean 0.5s)
    ttft = random.expovariate(2.0)
    await asyncio.sleep(ttft)
    
    # Simulate processing time based on prompt length
    processing_time = len(prompt) * 0.001
    await asyncio.sleep(processing_time)
    
    return f"[Synthetic Fallback] Successfully processed prompt for model {model_name}."

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True
)
async def _execute_llm_request(model_name: str, prompt: str, timeout: float):
    # litellm will automatically pick up API keys from the environment variables
    # mapped by pydantic or directly set in the .env file.
    response = await litellm.acompletion(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        timeout=timeout
    )
    return response.choices[0].message.content

async def generate_completion(model_name: str, prompt: str, is_shadow: bool = False) -> str:
    """
    Makes the async call to the LLM API using litellm.
    Falls back to synthetic response on failure.
    """
    if cb.is_open(model_name):
        logger.warning("Circuit breaker is OPEN. Fast-failing to synthetic fallback.", model=model_name)
        return await synthetic_fallback_response(prompt, model_name)
        
    # Shadow requests can tolerate longer timeouts
    timeout = 60.0 if is_shadow else 30.0

    try:
        response_text = await _execute_llm_request(model_name, prompt, timeout)
        cb.record_success(model_name)
        return response_text
    except Exception as e:
        logger.error("LLM request failed", model=model_name, error=str(e))
        cb.record_failure(model_name)
        return await synthetic_fallback_response(prompt, model_name)
