from pathlib import Path

import pytest
from scripts.run_deterministic_load import run_load_scenario


@pytest.mark.asyncio
async def test_sql_queue_completes_500_deterministic_jobs_without_stale_writes(
    tmp_path: Path,
) -> None:
    report = await run_load_scenario(
        job_count=500,
        seed=17,
        database_path=tmp_path / "load.db",
    )

    assert report["job_count"] == 500
    assert report["success_jobs"] == 460
    assert report["retried_jobs"] == 25
    assert report["stale_lease_jobs"] == 10
    assert report["worker_restart_jobs"] == 5
    assert report["stale_write_rejections"] == 15
    assert report["terminal_jobs"] == 500
    assert report["pending_jobs"] == 0
    assert report["dead_letter_jobs"] == 0
    assert report["lost_terminal_jobs"] == 0
