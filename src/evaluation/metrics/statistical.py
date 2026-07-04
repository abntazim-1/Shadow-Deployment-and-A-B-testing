import numpy as np
from scipy import stats
from typing import List, Dict, Any

def calculate_welchs_ttest(control_latencies: List[float], challenger_latencies: List[float]) -> Dict[str, Any]:
    """
    Computes Welch's t-test (assumes unequal variance) and Cohen's d for effect size.
    Returns p-value, t-statistic, cohen's d, and whether it is statistically significant (p < 0.05).
    """
    if len(control_latencies) < 2 or len(challenger_latencies) < 2:
        return {"error": "Not enough samples"}
        
    t_stat, p_value = stats.ttest_ind(control_latencies, challenger_latencies, equal_var=False)
    
    # Cohen's d calculation
    n1, n2 = len(control_latencies), len(challenger_latencies)
    var1, var2 = np.var(control_latencies, ddof=1), np.var(challenger_latencies, ddof=1)
    
    pooled_var = ((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2)
    
    # Handle edge case where variance is exactly zero
    if pooled_var == 0:
        cohens_d = 0.0
    else:
        mean1, mean2 = np.mean(control_latencies), np.mean(challenger_latencies)
        cohens_d = (mean1 - mean2) / np.sqrt(pooled_var)
        
    return {
        "p_value": float(p_value),
        "t_statistic": float(t_stat),
        "cohens_d": float(cohens_d),
        "significant": bool(p_value < 0.05)
    }
