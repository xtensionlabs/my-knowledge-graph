"""APScheduler registration tests — verify job set + triggers."""

from __future__ import annotations

import pytest

from synapse.scheduler import (
    JOB_ENERGY_REFRESH,
    JOB_HORIZON_REFRESH,
    JOB_LIBRARIAN_INTERVAL,
    JOB_SYNTHESIZER_DAILY,
    build_scheduler,
    list_jobs,
)


def test_build_scheduler_registers_all_four_jobs() -> None:
    sched = build_scheduler()
    ids = {j.id for j in sched.get_jobs()}
    assert JOB_SYNTHESIZER_DAILY in ids
    assert JOB_LIBRARIAN_INTERVAL in ids
    assert JOB_ENERGY_REFRESH in ids
    assert JOB_HORIZON_REFRESH in ids


def test_synthesizer_cron_runs_at_07_local() -> None:
    sched = build_scheduler()
    job = sched.get_job(JOB_SYNTHESIZER_DAILY)
    assert job is not None
    trigger = job.trigger
    assert hasattr(trigger, "fields")
    # CronTrigger exposes its fields as a list — find the `hour` field.
    field_names = {f.name: f for f in trigger.fields}
    assert "hour" in field_names
    assert str(field_names["hour"]) == "7"


def test_librarian_interval_is_two_hours() -> None:
    sched = build_scheduler()
    job = sched.get_job(JOB_LIBRARIAN_INTERVAL)
    assert job is not None
    # IntervalTrigger stores `interval` as a timedelta.
    interval = job.trigger.interval
    assert interval.total_seconds() == 2 * 3600


def test_list_jobs_returns_serializable_snapshot() -> None:
    rows = list_jobs()
    assert any(r["id"] == JOB_SYNTHESIZER_DAILY for r in rows)
    for r in rows:
        # All values are str or None — JSON-serializable.
        for v in r.values():
            assert v is None or isinstance(v, str)
