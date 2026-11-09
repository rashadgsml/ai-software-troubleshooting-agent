"""Secret redaction. Applied to every tool output BEFORE it reaches Claude or the DB."""

import re

REDACTED = "[REDACTED]"

_PATTERNS = [
    # credentials in URLs: scheme://user:password@host
    (re.compile(r"(?P<pre>[a-zA-Z][a-zA-Z0-9+.-]*://[^:/\s@]+:)[^@\s]+(?=@)"), r"\g<pre>" + REDACTED),
    # well-known key shapes
    (re.compile(r"\bsk[-_](?:live|test|ant)[-_A-Za-z0-9]+"), REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), "Bearer " + REDACTED),
    # key=value / "key": "value" for sensitive-looking keys
    (re.compile(r'(?i)("?(?:password|passwd|secret|token|api[_-]?key)"?\s*[:=]\s*)"?[^",\s}]+"?'),
     r"\g<1>" + f'"{REDACTED}"'),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
