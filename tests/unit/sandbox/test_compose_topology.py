from pathlib import Path

import yaml


def test_compose_isolates_worker_from_project_sandbox_daemon() -> None:
    compose_path = Path(__file__).parents[3] / "docker-compose.yml"
    text = compose_path.read_text(encoding="utf-8")
    compose = yaml.safe_load(text)
    services = compose["services"]

    worker_networks = set(services["worker"]["networks"])
    runner_networks = set(services["sandbox-runner"]["networks"])
    daemon_networks = set(services["sandbox-daemon"]["networks"])

    assert worker_networks.isdisjoint(daemon_networks)
    assert worker_networks & runner_networks == {"control"}
    assert runner_networks & daemon_networks == {"sandbox-daemon"}
    assert services["sandbox-daemon"].get("ports") is None
    assert "/var/run/docker.sock" not in text
    assert "@sha256:" in services["sandbox-daemon"]["image"]
