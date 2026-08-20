import pytest

from repo_maintenance_agent.domain.models import RiskLevel, ToolPermission
from repo_maintenance_agent.policies.risk import deterministic_risk


@pytest.mark.parametrize(
    ("path", "reason_prefix"),
    [
        (".github/workflows/ci.yml", "CI configuration"),
        ("src/auth/session.py", "authentication or security path"),
        ("migrations/001_users.sql", "database migration"),
        ("config/secrets.yaml", "sensitive configuration"),
    ],
)
def test_sensitive_paths_are_deterministically_high_risk(path: str, reason_prefix: str) -> None:
    risk, reasons = deterministic_risk((path,), (ToolPermission.REPO_READ,))

    assert risk is RiskLevel.HIGH
    assert any(reason.startswith(reason_prefix) for reason in reasons)
