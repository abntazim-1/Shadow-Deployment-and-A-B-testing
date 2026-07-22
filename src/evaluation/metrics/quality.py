import tiktoken
import json
import litellm
from typing import Dict, Any
from rouge_score import rouge_scorer
from src.core.logging import logger

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
    Evaluates lexical / token quality of responses.
    Checks token volume differences, empty responses, and ROUGE-L overlap.
    """
    control_tokens = get_token_count(control_resp)
    challenger_tokens = get_token_count(challenger_resp)
    
    # Calculate simple token differential
    token_diff = challenger_tokens - control_tokens
    
    # Basic semantic checks: empty response flags
    challenger_empty = len(challenger_resp.strip()) == 0
    control_empty = len(control_resp.strip()) == 0
    
    scorer = rouge_scorer.RougeScorer(['rougeL'], use_stemmer=True)
    scores = scorer.score(control_resp, challenger_resp)
    rouge_l = scores['rougeL'].fmeasure
    
    return {
        "control_tokens": control_tokens,
        "challenger_tokens": challenger_tokens,
        "token_differential": token_diff,
        "challenger_is_empty": challenger_empty,
        "control_is_empty": control_empty,
        "rouge_l": rouge_l
    }

async def evaluate_semantic_quality_with_judge(
    prompt: str, 
    control_resp: str, 
    challenger_resp: str,
    judge_model: str = "groq/llama-3.1-8b-instant"
) -> Dict[str, Any]:
    """
    Evaluates semantic quality using an LLM-as-a-Judge model.
    Returns structured scores (1-5 scale) for factuality, semantic equivalence, and reasoning.
    """
    base_metrics = calculate_quality_metrics(prompt, control_resp, challenger_resp)
    
    # Fast path fallback if either response is empty
    if base_metrics["challenger_is_empty"] or base_metrics["control_is_empty"]:
        base_metrics.update({
            "judge_score": 1.0 if base_metrics["challenger_is_empty"] else 5.0,
            "semantic_equivalence": 0.0,
            "judge_reasoning": "One or both responses were empty."
        })
        return base_metrics

    judge_prompt = f"""You are an expert AI evaluator conducting a side-by-side quality assessment.
Evaluate the Challenger Model Response against the Control Model Response for the given Prompt.

PROMPT:
{prompt}

CONTROL MODEL RESPONSE:
{control_resp}

CHALLENGER MODEL RESPONSE:
{challenger_resp}

Return a valid JSON object strictly matching this schema:
{{
  "semantic_equivalence": <float 0.0 to 1.0>,
  "judge_score": <float 1.0 to 5.0>,
  "reasoning": "<concise explanation>"
}}
"""
    try:
        response = await litellm.acompletion(
            model=judge_model,
            messages=[{"role": "user", "content": judge_prompt}],
            timeout=15.0,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        parsed = json.loads(content)
        base_metrics.update({
            "judge_score": float(parsed.get("judge_score", 4.0)),
            "semantic_equivalence": float(parsed.get("semantic_equivalence", base_metrics["rouge_l"])),
            "judge_reasoning": str(parsed.get("reasoning", "Semantic judge completed successfully."))
        })
    except Exception as e:
        logger.warning("LLM Judge call failed/bypassed, using heuristic score", error=str(e))
        # Heuristic fallback based on Rouge-L
        heuristic_score = round(1.0 + 4.0 * base_metrics["rouge_l"], 2)
        base_metrics.update({
            "judge_score": heuristic_score,
            "semantic_equivalence": base_metrics["rouge_l"],
            "judge_reasoning": f"Heuristic evaluation score (Rouge-L: {base_metrics['rouge_l']:.2f})."
        })

    return base_metrics

