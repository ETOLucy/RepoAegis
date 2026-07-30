from repo_maintenance_agent.observability.tracing import InMemoryTraceSink, StructuredTracer


def test_tracer_redacts_credentials_and_records_version_dimensions() -> None:
    sink = InMemoryTraceSink()
    tracer = StructuredTracer(
        sink=sink,
        model_id="gpt-test",
        prompt_hash="a" * 64,
        tool_schema_version="1",
        policy_version="1",
    )

    tracer.emit(
        "tool.completed",
        {
            "tenant_id": "tenant-a",
            "authorization": "Bearer secret-value",
            "token_count": 15,
        },
    )

    event = sink.events[0]
    assert event.attributes["authorization"] == "[REDACTED]"
    assert "secret-value" not in event.model_dump_json()
    assert event.model_id == "gpt-test"
    assert event.attributes["token_count"] == 15
