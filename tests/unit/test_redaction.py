"""Redaction audit: a corpus of real-world secret formats must never survive; ordinary
diagnostic content must."""

import pytest

from app.core.redaction import REDACTED, redact, redact_json

SECRETS = {
    "url password": ("postgresql://shop:S3cretPw!@db.internal:5432/shop", "S3cretPw"),
    "url password (amqp)": ("amqp://user:hunter2hunter2@mq:5672/", "hunter2hunter2"),
    "stripe live key": ("key sk_live_51HxExampleSecretKey in log", "sk_live_51HxExampleSecretKey"),
    "anthropic key": ("using sk-ant-api03-AbCdEf123456_-xyz now", "AbCdEf123456"),
    "aws access key id": ("AWS key AKIAIOSFODNN7EXAMPLE leaked", "AKIAIOSFODNN7EXAMPLE"),
    "aws temp key id": ("ASIAIOSFODNN7EXAMPLE", "ASIAIOSFODNN7EXAMPLE"),
    "github token": ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnopqrstuv"),
    "slack token": ("xoxb-123456789012-abcdefghijkl", "xoxb-123456789012"),
    "jwt": ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fw", "SflKxwRJSMeKKF2QT4fw"),
    "bearer header": ("Authorization: Bearer abc.def-ghi_jkl123", "abc.def-ghi_jkl123"),
    "json password": ('{"db_password": "p@ssw0rd-value"}', "p@ssw0rd-value"),
    "env style secret": ("CLIENT_SECRET=0123456789abcdef", "0123456789abcdef"),
    "api key kv": ("api_key: Zm9vYmFyYmF6", "Zm9vYmFyYmF6"),
    "access key kv": ("aws_access_key_id=AKIAXXXXXXXXXXXXXXXX", "AKIAXXXXXXXXXXXXXXXX"),
    "private key block": ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA7\nabc\n-----END RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA7"),
}


@pytest.mark.parametrize("name", SECRETS)
def test_secret_format_is_redacted(name):
    text, secret = SECRETS[name]
    out = redact(text)
    assert secret not in out, f"{name} leaked: {out}"
    assert REDACTED in out


@pytest.mark.parametrize("benign", [
    "disk_used_pct 99.8 on /var/log/shop",
    "QueuePool limit of size 10 overflow 0 reached, connection timed out, timeout 30.00",
    "Name or service not known: payments.internall",
    "POST /checkout 500 TimeoutError: could not acquire DB connection",
    "v2.15.1 changed payment_service_url from https://payments.internal/v1",
    "idle_in_transaction: 8, waiting_requests: 37",
])
def test_diagnostic_content_is_preserved(benign):
    assert redact(benign) == benign


def test_redact_json_walks_nested_structures():
    data = {"a": ["ok", {"b": "postgresql://u:pw123456@h/db"}], "n": 5, "t": "token=abcd1234"}
    out = redact_json(data)
    assert "pw123456" not in str(out) and "abcd1234" not in str(out)
    assert out["n"] == 5 and out["a"][0] == "ok"


def test_redaction_is_idempotent():
    once = redact("postgresql://u:secretpw@h/db and sk_live_abc123")
    assert redact(once) == once
