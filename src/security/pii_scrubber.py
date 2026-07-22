import re
from typing import Dict

# Pre-compiled high-performance regular expressions for PII detection
PII_PATTERNS: Dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", re.IGNORECASE),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "PHONE": re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "API_KEY": re.compile(r"\b(?:sk-[a-zA-Z0-9]{32,}|AIzaSy[a-zA-Z0-9_-]{33}|gsk_[a-zA-Z0-9]{32,}|bearer\s+[a-zA-Z0-9._-]{20,})\b", re.IGNORECASE),
    "IPV4": re.compile(r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"),
}

def sanitize_pii(text: str) -> str:
    """
    Sanitizes sensitive Personally Identifiable Information (PII) from prompt strings
    before transmitting payloads to third-party shadow LLM providers.
    Complying with GDPR, HIPAA, and Enterprise Data Protection policies.
    """
    if not text:
        return ""

    sanitized = text
    for pii_type, pattern in PII_PATTERNS.items():
        placeholder = f"[{pii_type}_REDACTED]"
        sanitized = pattern.sub(placeholder, sanitized)

    return sanitized
