"""Obsolete cron failure notices must not replay after a successful run."""
import time
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from gateway import delivery_ledger as dl
from tests.gateway.test_delivery_ledger import _record, _row, _orphan

NOTICE = (
    "Cronjob Response: monitor\n(job_id: example-job)\n-------------\n\n"
    "⚠️ Cron 'monitor' failed: provider unavailable."
)


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "_db_path", lambda: tmp_path / "state.db")


@pytest.mark.parametrize("runtime", [False, True])
def test_success_after_notice_prevents_replay(runtime):
    now = time.time()
    _record(session_key="cron:telegram:C1", platform="telegram", content=NOTICE)
    dl.mark_failed("ob-1", "send_path_degraded")
    if not runtime:
        _orphan("ob-1")
    job = {"last_status": "ok", "last_run_at": datetime.fromtimestamp(now + 10, UTC).isoformat()}
    with patch("cron.jobs.get_job", return_value=job):
        result = (
            dl.sweep_failed_for_runtime("telegram", now=now + 60)
            if runtime else dl.sweep_recoverable(now=now + 60)
        )
    assert result == []
    row = _row("ob-1")
    assert row is not None and row["state"] == "abandoned"


@pytest.mark.parametrize("runtime", [False, True])
@pytest.mark.parametrize("case", [
    "still_failing", "older_success", "missing_job", "invalid_time", "naive_time",
    "balance_alert", "ordinary_reply", "store_error",
])
def test_recovery_preserved_without_proof_of_obsolescence(runtime, case):
    now = time.time()
    content = NOTICE if case != "balance_alert" else NOTICE.split("⚠️")[0] + "Balance below threshold."
    session_key = "agent:telegram:C1" if case == "ordinary_reply" else "cron:telegram:C1"
    _record(session_key=session_key, platform="telegram", content=content)
    dl.mark_failed("ob-1", "send_path_degraded")
    if not runtime:
        _orphan("ob-1")
    recent = {"last_status": "ok", "last_run_at": datetime.fromtimestamp(now + 10, UTC).isoformat()}
    job = {
        "still_failing": {**recent, "last_status": "error"},
        "older_success": {**recent, "last_run_at": datetime.fromtimestamp(now - 10, UTC).isoformat()},
        "missing_job": None,
        "invalid_time": {**recent, "last_run_at": "not a date"},
        "naive_time": {**recent, "last_run_at": "2025-01-01T13:00:00"},
    }.get(case, recent)
    error = OSError("unavailable") if case == "store_error" else None
    with patch("cron.jobs.get_job", return_value=job, side_effect=error):
        result = (
            dl.sweep_failed_for_runtime("telegram", now=now + 60)
            if runtime else dl.sweep_recoverable(now=now + 60)
        )
    assert len(result) == 1
