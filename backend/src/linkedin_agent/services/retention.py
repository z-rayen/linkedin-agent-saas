"""Retention of stored results, and the deletion of a user's data with the account.

Retention: finished research and watch runs, and watch results, older than `result_retention_days`, are purged.
Application runs and applications are the user's own work: they are never purged by this job, only removed with the
account.

Account deletion: every row that belongs to the user is removed, in an order that respects the foreign keys (runs before
the applications they point to), together with the workflow checkpoints of the user's runs. Other users' data is not
touched. The deletion is one transaction: it either removes everything or nothing.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import models
from ..clock import utcnow
from ..core.settings import get_settings
from ..database import SessionLocal

logger = logging.getLogger(__name__)
FINISHED_STATUSES = ("done", "closed", "failed")
RETAINED_KINDS = ("research", "watch")  # application runs are the user's applications: never purged by the job
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def retention_cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def _delete_checkpoints(db: Session, thread_ids: list[str]) -> int:
    """The workflow checkpoints of the given runs. The integer run id is the thread id."""
    if not thread_ids:
        return 0
    removed = 0
    for table in CHECKPOINT_TABLES:
        result = db.execute(text(f"DELETE FROM {table} WHERE thread_id = ANY(:ids)"), {"ids": thread_ids})
        removed += int(result.rowcount or 0)
    return removed


def purge_expired(db: Session, *, now: datetime, days: int) -> dict[str, int]:
    """Delete finished research and watch runs and watch results older than the retention period. Returns the counts."""
    limit = retention_cutoff(now, days)
    expired_runs = (
        db.query(models.PreparationRun.id)
        .filter(
            models.PreparationRun.kind.in_(RETAINED_KINDS),
            models.PreparationRun.status.in_(FINISHED_STATUSES),
            models.PreparationRun.updated_at < limit,
        )
        .all()
    )
    run_ids = [str(row.id) for row in expired_runs]
    checkpoints = _delete_checkpoints(db, run_ids)
    runs = (
        db.query(models.PreparationRun)
        .filter(models.PreparationRun.id.in_([int(run_id) for run_id in run_ids]))
        .delete(synchronize_session=False)
        if run_ids
        else 0
    )
    watch_results = (
        db.query(models.WatchResult).filter(models.WatchResult.created_at < limit).delete(synchronize_session=False)
    )
    db.commit()
    counts = {"runs": int(runs), "watch_results": int(watch_results), "checkpoints": checkpoints}
    logger.info(
        "retention purge runs=%d watch_results=%d checkpoints=%d", counts["runs"], counts["watch_results"], checkpoints
    )
    return counts


def delete_user_data(db: Session, user_id: int) -> dict[str, int]:
    """Remove everything that belongs to the user, including the account. One transaction: all or nothing."""
    try:
        run_rows = db.query(models.PreparationRun.id).filter(models.PreparationRun.user_id == user_id).all()
        counts = {"checkpoints": _delete_checkpoints(db, [str(row.id) for row in run_rows])}
        counts["runs"] = (
            db.query(models.PreparationRun)
            .filter(models.PreparationRun.user_id == user_id)
            .delete(synchronize_session=False)
        )
        counts["applications"] = (
            db.query(models.Application).filter(models.Application.user_id == user_id).delete(synchronize_session=False)
        )
        counts["watch_results"] = (
            db.query(models.WatchResult).filter(models.WatchResult.user_id == user_id).delete(synchronize_session=False)
        )
        counts["agent_messages"] = (
            db.query(models.AgentMessage)
            .filter(models.AgentMessage.user_id == user_id)
            .delete(synchronize_session=False)
        )
        counts["content_queue"] = (
            db.query(models.ContentQueue)
            .filter(models.ContentQueue.user_id == user_id)
            .delete(synchronize_session=False)
        )
        counts["profile_knowledge"] = (
            db.query(models.ProfileKnowledge)
            .filter(models.ProfileKnowledge.user_id == user_id)
            .delete(synchronize_session=False)
        )
        counts["oauth_tokens"] = (
            db.query(models.OAuthToken).filter(models.OAuthToken.user_id == user_id).delete(synchronize_session=False)
        )
        counts["profile_config"] = (
            db.query(models.ProfileConfig)
            .filter(models.ProfileConfig.user_id == user_id)
            .delete(synchronize_session=False)
        )
        counts["users"] = db.query(models.User).filter(models.User.id == user_id).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("account deleted rows=%s", {key: value for key, value in counts.items()})
    return counts


def _purge_once() -> dict[str, int]:
    with SessionLocal() as db:
        return purge_expired(db, now=utcnow(), days=get_settings().result_retention_days)


async def purge_forever() -> None:
    """The retention job: once at startup, then every `result_purge_interval_seconds`. A failure is logged and retried
    at the next interval; it never stops the application."""
    while True:
        try:
            await asyncio.to_thread(_purge_once)
        except Exception as exc:
            logger.error("retention purge failed error_type=%s", type(exc).__name__)
        await asyncio.sleep(get_settings().result_purge_interval_seconds)
