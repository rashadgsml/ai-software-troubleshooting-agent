"""Secret redaction.

Applied (defence in depth) at every boundary where text can reach Claude or storage:
  1. tool output, before it goes to the model          (ToolRegistry.call)
  2. user-submitted incident text, before it is stored  (POST /incidents)
  3. every persisted step and the final report          (investigation job)
Patterns are deliberately conservative: false positives only hide a value, false
negatives leak a credential.
"""

import re
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS = [
    # PEM private key blocks (multi-line)
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), REDACTED),
    # credentials in URLs: scheme://user:password@host
    (re.compile(r"(?P<pre>[a-zA-Z][a-zA-Z0-9+.-]*://[^:/\s@]+:)[^@\s]+(?=@)"), r"\g<pre>" + REDACTED),
    # well-known key shapes
    (re.compile(r"\bsk[-_](?:live|test|ant)[-_A-Za-z0-9]+"), REDACTED),  # Stripe / Anthropic
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),  # AWS access key id
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), REDACTED),  # GitHub tokens
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), REDACTED),  # Slack tokens
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), REDACTED),  # JWT
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer " + REDACTED),
    # key=value / "key": "value" for sensitive-looking keys (password, secret, token, api key, ...)
    (re.compile(
        r'(?i)("?[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[\w.-]*"?'
        r'\s*[:=]\s*)"?[^",\s}]+"?'
    ), r"\g<1>" + f'"{REDACTED}"'),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_json(value: Any) -> Any:
    """Redact every string inside a JSON-like structure (dict keys are left alone)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_json(v) for v in value]
    return value
