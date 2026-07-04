import hashlib

def is_challenger_assigned(user_id: str, experiment_salt: str, traffic_weight: float) -> bool:
    """
    Deterministic A/B routing using SHA-256 hashing on user_id + salt.
    """
    if traffic_weight <= 0.0:
        return False
    if traffic_weight >= 1.0:
        return True
        
    hash_input = f"{user_id}{experiment_salt}".encode('utf-8')
    hash_value = int(hashlib.sha256(hash_input).hexdigest(), 16)
    
    # modulo 100 for percentage (0-99)
    bucket = hash_value % 100
    
    # compare against weight * 100
    return bucket < (traffic_weight * 100)
