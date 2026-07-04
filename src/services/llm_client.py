import httpx
import asyncio
import time
import random
from typing import Dict
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from src.core.logging import logger

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, cooldown_secs: int = 60):
        self.failure_threshold = failure_threshold
        self.cooldown_secs = cooldown_secs
        
        # State tracking per model URL
        self._failures: Dict[str, int] = {}
        self._last_failure_time: Dict[str, float] = {}

    def is_open(self, url: str) -> bool:
        failures = self._failures.get(url, 0)
        if failures >= self.failure_threshold:
            last_fail = self._last_failure_time.get(url, 0)
            if time.time() - last_fail < self.cooldown_secs:
                return True
            else:
                # Cooldown expired, half-open state
                self._failures[url] = self.failure_threshold - 1
                return False
        return False

    def record_failure(self, url: str):
        self._failures[url] = self._failures.get(url, 0) + 1
        self._last_failure_time[url] = time.time()
        logger.warning("Circuit breaker recorded failure", url=url, failures=self._failures[url])

    def record_success(self, url: str):
        if url in self._failures:
            self._failures[url] = 0
            self._last_failure_time[url] = 0

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
    retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException)),
    reraise=True
)
async def _execute_http_request(url: str, payload: dict, timeout: float):
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()

async def generate_completion(model_name: str, url: str, prompt: str, is_shadow: bool = False) -> str:
    """
    Makes the async call to Ollama.
    Falls back to synthetic response on failure.
    """
    if cb.is_open(url):
        logger.warning("Circuit breaker is OPEN. Fast-failing to synthetic fallback.", url=url)
        return await synthetic_fallback_response(prompt, model_name)
        
    # Shadow requests can tolerate longer timeouts
    timeout = 60.0 if is_shadow else 30.0
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False
    }

    try:
        response_data = await _execute_http_request(url, payload, timeout)
        cb.record_success(url)
        return response_data.get("response", "")
    except Exception as e:
        logger.error("LLM request failed", model=model_name, url=url, error=str(e))
        cb.record_failure(url)
        return await synthetic_fallback_response(prompt, model_name)
