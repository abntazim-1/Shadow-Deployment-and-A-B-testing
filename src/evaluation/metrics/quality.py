import tiktoken
from typing import Dict, Any

def get_token_count(text: str, model: str = "cl100k_base") -> int:
    """Calculates approximate token count using tiktoken."""
    try:
        encoding = tiktoken.get_encoding(model)
        return len(encoding.encode(text))
    except Exception:
        # Fallback approximation if encoding fails
        return len(text.split())

def calculate_quality_metrics(prompt: str, control_resp: str, challenger_resp: str) -> Dict[str, Any]:
    """
    Evaluates semantic / token quality of responses.
    This is an initial implementation that checks token volume differences and empty responses.
    """
    control_tokens = get_token_count(control_resp)
    challenger_tokens = get_token_count(challenger_resp)
    
    # Calculate simple token differential
    token_diff = challenger_tokens - control_tokens
    
    # Basic semantic checks: did it hallucinate a massive/empty response?
    challenger_empty = len(challenger_resp.strip()) == 0
    control_empty = len(control_resp.strip()) == 0
    
    return {
        "control_tokens": control_tokens,
        "challenger_tokens": challenger_tokens,
        "token_differential": token_diff,
        "challenger_is_empty": challenger_empty,
        "control_is_empty": control_empty
    }
