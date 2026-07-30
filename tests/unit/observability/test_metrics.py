from concurrent.futures import ThreadPoolExecutor

from repo_maintenance_agent.observability.metrics import InMemoryMetrics


def test_metrics_normalize_label_order_and_increment_thread_safely() -> None:
    metrics = InMemoryMetrics()

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda _: metrics.increment(
                    "tool_calls",
                    labels={"status": "ok", "tool": "search"},
                ),
                range(100),
            )
        )

    assert (
        metrics.value(
            "tool_calls",
            labels={"tool": "search", "status": "ok"},
        )
        == 100
    )
