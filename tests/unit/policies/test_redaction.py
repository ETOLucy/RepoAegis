from repo_maintenance_agent.policies.redaction import Redactor


def test_redactor_removes_nested_credentials_without_mutating_input() -> None:
    payload = {
        "authorization": "Bearer live-secret",
        "nested": {"api_key": "sk-example-not-real", "message": "safe"},
        "command": "curl -H 'Authorization: Bearer abc123' https://example.invalid",
    }

    redacted = Redactor().redact(payload)

    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert "abc123" not in redacted["command"]
    assert payload["authorization"] == "Bearer live-secret"


def test_redactor_does_not_hide_non_secret_token_metrics() -> None:
    redacted = Redactor().redact({"token_count": 42, "duration_ms": 12})

    assert redacted == {"token_count": 42, "duration_ms": 12}
