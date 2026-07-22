import pytest
from src.security.pii_scrubber import sanitize_pii

def test_sanitize_email():
    raw = "Please contact user at john.doe@example.com for further info."
    cleaned = sanitize_pii(raw)
    assert "john.doe@example.com" not in cleaned
    assert "[EMAIL_REDACTED]" in cleaned

def test_sanitize_credit_card():
    raw = "My card number is 4532-1123-9842-1092 please process."
    cleaned = sanitize_pii(raw)
    assert "4532-1123-9842-1092" not in cleaned
    assert "[CREDIT_CARD_REDACTED]" in cleaned

def test_sanitize_ssn():
    raw = "SSN identifier: 123-45-6789"
    cleaned = sanitize_pii(raw)
    assert "123-45-6789" not in cleaned
    assert "[SSN_REDACTED]" in cleaned

def test_sanitize_api_key():
    raw = "Use key gsk_12345678901234567890123456789012 to connect."
    cleaned = sanitize_pii(raw)
    assert "gsk_12345678901234567890123456789012" not in cleaned
    assert "[API_KEY_REDACTED]" in cleaned

def test_sanitize_multiple_pii_types():
    raw = "Email: alice@work.io, IP: 192.168.1.1, Phone: 555-123-4567"
    cleaned = sanitize_pii(raw)
    assert "alice@work.io" not in cleaned
    assert "192.168.1.1" not in cleaned
    assert "555-123-4567" not in cleaned
    assert "[EMAIL_REDACTED]" in cleaned
    assert "[IPV4_REDACTED]" in cleaned
    assert "[PHONE_REDACTED]" in cleaned
