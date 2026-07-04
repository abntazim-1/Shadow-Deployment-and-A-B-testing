import numpy as np
from src.evaluation.metrics.statistical import calculate_welchs_ttest

def test_welchs_ttest_significant():
    """Test with distinct distributions to guarantee statistical significance."""
    np.random.seed(42)
    # Control: mean 100, std 10
    control = np.random.normal(100, 10, 50).tolist()
    # Challenger: mean 50, std 10
    challenger = np.random.normal(50, 10, 50).tolist()
    
    result = calculate_welchs_ttest(control, challenger)
    
    assert "error" not in result
    assert result["significant"] is True
    assert result["p_value"] < 0.05
    assert result["cohens_d"] > 0, "Control mean > challenger mean, Cohen's d should be positive"

def test_welchs_ttest_insignificant():
    """Test with identical distributions to guarantee no statistical significance."""
    np.random.seed(42)
    # Control and Challenger drawn from identical distribution
    control = np.random.normal(100, 10, 50).tolist()
    challenger = np.random.normal(100, 10, 50).tolist()
    
    result = calculate_welchs_ttest(control, challenger)
    
    assert "error" not in result
    assert result["significant"] is False
    assert result["p_value"] >= 0.05

def test_welchs_ttest_not_enough_samples():
    """Test the edge case where there are insufficient samples."""
    control = [100.0]
    challenger = [50.0]
    
    result = calculate_welchs_ttest(control, challenger)
    assert "error" in result
    assert result["error"] == "Not enough samples"

def test_zero_variance():
    """Test when distributions have zero variance."""
    control = [100.0, 100.0, 100.0]
    challenger = [100.0, 100.0, 100.0]
    
    result = calculate_welchs_ttest(control, challenger)
    assert "error" not in result
    assert result["cohens_d"] == 0.0
