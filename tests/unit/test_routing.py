from src.routing.strategies import is_challenger_assigned

def test_deterministic_hashing():
    """Test that the same user always gets the same bucket"""
    user_id = "user_alpha_123"
    salt = "secret_salt"
    weight = 0.5
    
    result1 = is_challenger_assigned(user_id, salt, weight)
    result2 = is_challenger_assigned(user_id, salt, weight)
    
    assert result1 == result2, "Hashing should be deterministic for the same user"

def test_weight_extremes():
    """Test that 0% and 100% traffic weights work as expected"""
    user_id = "user_beta_456"
    salt = "secret_salt"
    
    assert not is_challenger_assigned(user_id, salt, 0.0), "Weight 0.0 should always return False"
    assert is_challenger_assigned(user_id, salt, 1.0), "Weight 1.0 should always return True"

def test_distribution():
    """Test that the distribution roughly matches the weight across many users"""
    salt = "distribution_salt"
    weight = 0.3
    challenger_count = 0
    total_users = 1000
    
    for i in range(total_users):
        user_id = f"user_{i}"
        if is_challenger_assigned(user_id, salt, weight):
            challenger_count += 1
            
    ratio = challenger_count / total_users
    # Allow some margin of error due to hashing
    assert 0.25 <= ratio <= 0.35, f"Expected ~30% in challenger, got {ratio * 100}%"
