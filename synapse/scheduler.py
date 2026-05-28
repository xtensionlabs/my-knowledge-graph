"""APScheduler wiring — the cron + interval brain.

Registers the Synapse job set on an AsyncIOScheduler with a SQLite job store
so jobs survive process restarts.

Job set:
    - synthesizer_daily   — cron 07:00 Africa/Nairobi → Delta Briefing
    - librarian_interval  — every 2h → Librarian sweeps inbox/
    - energy_refresh      — every 30 min → energy estimate refresh
    - horizon_refresh     — every 1 h → Horizon queue refresh
    - guardian_interval   — every 4h → burnout watchdog (M4)
    - strategist_weekly   — cron Sunday 18:00 local → Strategist (M4)
    - consolidator_nightly — cron 02:00 local → Synthesizer.consolidate (M4)
    - hebbian_decay       — cron 03:00 local → edge decay sweep (M4)

Started by `synapse start` lifespan alongside gateway + Telegram bot.
"""

from __future__ import annotations

from typing import Any

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from synapse.config import (
    CONSOLIDATION_HOUR,
    CONSOLIDATION_MINUTE,
    DELTA_BRIEFING_HOUR,
    DELTA_BRIEFING_MINUTE,
    ENERGY_REFRESH_INTERVAL_MINUTES,
    GUARDIAN_SCHEDULE_INTERVAL_HOURS,
    HEBBIAN_DECAY_SCHEDULE_HOUR,
    HEBBIAN_DECAY_SCHEDULE_MINUTE,
    LIBRARIAN_SCHEDULE_INTERVAL_HOURS,
    STRATEGIST_SCHEDULE_DAY_OF_WEEK,
    STRATEGIST_SCHEDULE_HOUR,
    SYNAPSE_TIMEZONE,
    get_settings,
)

# Job ids — also used to look them up / replace existing ones.
JOB_SYNTHESIZER_DAILY = "synthesizer_daily"
JOB_LIBRARIAN_INTERVAL = "librarian_interval"
JOB_ENERGY_REFRESH = "energy_refresh"
JOB_HORIZON_REFRESH = "horizon_refresh"
JOB_GUARDIAN_INTERVAL = "guardian_interval"
JOB_STRATEGIST_WEEKLY = "strategist_weekly"
JOB_CONSOLIDATOR_NIGHTLY = "consolidator_nightly"
JOB_HEBBIAN_DECAY = "hebbian_decay"

_scheduler: AsyncIOScheduler | None = None


# ── Job entry points (top-level so APScheduler can pickle the reference) ────


async def _run_synthesizer() -> None:
    """Daily Delta Briefing trigger."""
    from synapse.agents.synthesizer import synthesizer

    try:
        result = await synthesizer.run()
        logger.info(
            "[scheduler] synthesizer: {ok} {summary}",
            ok="✓" if result.ok else "✗",
            summary=result.summary,
        )
        # Push the brief to Telegram if a chat is wired up.
        await _push_brief_to_telegram(result.artifacts.get("brief_markdown", ""))
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scheduler] synthesizer crashed: {exc}", exc=exc)


async def _run_librarian() -> None:
    """Periodic Librarian sweep."""
    from synapse.agents.librarian import librarian

    try:
        result = await librarian.run()
        logger.info(
            "[scheduler] librarian: {summary}",
            summary=result.summary,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scheduler] librarian crashed: {exc}", exc=exc)


async def _run_energy_refresh() -> None:
    """Recompute energy estimate (M2 stub returns 'medium')."""
    from synapse.context.energy import refresh_energy_estimate

    try:
        await refresh_energy_estimate()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduler] energy refresh failed: {exc}", exc=exc)


async def _run_horizon_refresh() -> None:
    """Re-walk EVENTs to update the session-state Horizon queue + trigger pre-loads."""
    from synapse.context.horizon import refresh_horizon

    try:
        n = refresh_horizon()
        logger.debug("[scheduler] horizon refresh: {n} upcoming events", n=n)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduler] horizon refresh failed: {exc}", exc=exc)


async def _run_guardian() -> None:
    """Burnout watchdog — runs every 4 hours."""
    from synapse.agents.guardian import guardian

    try:
        result = await guardian.run()
        logger.info("[scheduler] guardian: {summary}", summary=result.summary)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scheduler] guardian crashed: {exc}", exc=exc)


async def _run_strategist() -> None:
    """Weekly strategist — Sunday 18:00 local."""
    from synapse.agents.strategist import strategist

    try:
        result = await strategist.run()
        logger.info("[scheduler] strategist: {summary}", summary=result.summary)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scheduler] strategist crashed: {exc}", exc=exc)


async def _run_consolidator() -> None:
    """Nightly consolidation — Synthesizer.consolidate() at 02:00 local."""
    from synapse.agents.synthesizer import synthesizer

    try:
        result = await synthesizer.consolidate()
        logger.info("[scheduler] consolidator: {summary}", summary=result.summary)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scheduler] consolidator crashed: {exc}", exc=exc)


def _run_hebbian_decay() -> None:
    """Decay un-touched edges; sync (no I/O) so AsyncIO scheduling is trivial."""
    from synapse.graph.hebbian import decay_old_edges

    try:
        result = decay_old_edges()
        logger.info(
            "[scheduler] hebbian decay: examined={ex} decayed={d}",
            ex=result.edges_examined,
            d=result.edges_decayed,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scheduler] hebbian decay crashed: {exc}", exc=exc)


async def _push_brief_to_telegram(markdown: str) -> None:
    """Best-effort delivery of the Delta Briefing to the configured chat.

    Uses the same bot token. Targets `TELEGRAM_ALLOWED_USER_ID` if set — that
    happens to also be the user's chat id for a 1:1 bot, which is the only
    supported configuration (Synapse is single-user).
    """
    if not markdown.strip():
        return
    settings = get_settings()
    if not settings.telegram_bot_token or settings.telegram_allowed_user_id is None:
        logger.info("[scheduler] no telegram chat configured; skipping brief push")
        return
    try:
        from telegram import Bot

        from synapse.capture.telegram_bot import _chunk_markdown

        bot = Bot(token=settings.telegram_bot_token)
        async with bot:
            for chunk in _chunk_markdown(markdown):
                await bot.send_message(
                    chat_id=settings.telegram_allowed_user_id, text=chunk
                )
        logger.info("[scheduler] delta briefing pushed to telegram")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scheduler] telegram push failed: {exc}", exc=exc)


# ── Scheduler lifecycle ──────────────────────────────────────────────────────


def build_scheduler() -> AsyncIOScheduler:
    """Construct (but don't start) the scheduler with the standard Synapse job set."""
    settings = get_settings()

    job_store = SQLAlchemyJobStore(url=settings.db_url)
    scheduler = AsyncIOScheduler(
        timezone=SYNAPSE_TIMEZONE,
        jobstores={"default": job_store},
        executors={"default": AsyncIOExecutor()},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60 * 30},
    )

    scheduler.add_job(
        _run_synthesizer,
        trigger=CronTrigger(
            hour=DELTA_BRIEFING_HOUR,
            minute=DELTA_BRIEFING_MINUTE,
            timezone=SYNAPSE_TIMEZONE,
        ),
        id=JOB_SYNTHESIZER_DAILY,
        replace_existing=True,
        name="Synthesizer — daily Delta Briefing",
    )
    scheduler.add_job(
        _run_librarian,
        trigger=IntervalTrigger(hours=LIBRARIAN_SCHEDULE_INTERVAL_HOURS),
        id=JOB_LIBRARIAN_INTERVAL,
        replace_existing=True,
        name="Librarian — inbox sweep",
    )
    scheduler.add_job(
        _run_energy_refresh,
        trigger=IntervalTrigger(minutes=ENERGY_REFRESH_INTERVAL_MINUTES),
        id=JOB_ENERGY_REFRESH,
        replace_existing=True,
        name="Energy estimate refresh",
    )
    scheduler.add_job(
        _run_horizon_refresh,
        trigger=IntervalTrigger(hours=1),
        id=JOB_HORIZON_REFRESH,
        replace_existing=True,
        name="Horizon queue refresh",
    )
    # M4 jobs
    scheduler.add_job(
        _run_guardian,
        trigger=IntervalTrigger(hours=GUARDIAN_SCHEDULE_INTERVAL_HOURS),
        id=JOB_GUARDIAN_INTERVAL,
        replace_existing=True,
        name="Guardian — burnout watchdog (4h)",
    )
    scheduler.add_job(
        _run_strategist,
        trigger=CronTrigger(
            day_of_week=STRATEGIST_SCHEDULE_DAY_OF_WEEK,
            hour=STRATEGIST_SCHEDULE_HOUR,
            minute=0,
            timezone=SYNAPSE_TIMEZONE,
        ),
        id=JOB_STRATEGIST_WEEKLY,
        replace_existing=True,
        name="Strategist — weekly planning (Sun 18:00)",
    )
    scheduler.add_job(
        _run_consolidator,
        trigger=CronTrigger(
            hour=CONSOLIDATION_HOUR,
            minute=CONSOLIDATION_MINUTE,
            timezone=SYNAPSE_TIMEZONE,
        ),
        id=JOB_CONSOLIDATOR_NIGHTLY,
        replace_existing=True,
        name="Consolidator — nightly abstraction pass (02:00)",
    )
    scheduler.add_job(
        _run_hebbian_decay,
        trigger=CronTrigger(
            hour=HEBBIAN_DECAY_SCHEDULE_HOUR,
            minute=HEBBIAN_DECAY_SCHEDULE_MINUTE,
            timezone=SYNAPSE_TIMEZONE,
        ),
        id=JOB_HEBBIAN_DECAY,
        replace_existing=True,
        name="Hebbian decay — nightly edge weight decay (03:00)",
    )

    return scheduler


def start_scheduler() -> AsyncIOScheduler:
    """Build + start the global scheduler. Idempotent."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    _scheduler = build_scheduler()
    _scheduler.start()
    logger.info(
        "scheduler started ({tz}, {n} jobs)",
        tz=SYNAPSE_TIMEZONE,
        n=len(_scheduler.get_jobs()),
    )
    return _scheduler


def shutdown_scheduler(*, wait: bool = False) -> None:
    """Stop the global scheduler if running."""
    global _scheduler
    if _scheduler is None:
        return
    if _scheduler.running:
        _scheduler.shutdown(wait=wait)
    _scheduler = None


def list_jobs() -> list[dict[str, Any]]:
    """Return a serializable snapshot of registered jobs (live or built-on-demand).

    `job.next_run_time` is only set on jobs that have been scheduled by a running
    scheduler; on jobs built off a stopped scheduler the attribute may not exist.
    """
    scheduler = _scheduler if (_scheduler is not None and _scheduler.running) else build_scheduler()
    out: list[dict[str, Any]] = []
    for job in scheduler.get_jobs():
        next_run = getattr(job, "next_run_time", None)
        out.append(
            {
                "id": job.id,
                "name": job.name,
                "trigger": str(job.trigger),
                "next_run_time": next_run.isoformat() if next_run else None,
            }
        )
    return out
