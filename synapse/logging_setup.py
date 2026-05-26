"""Configure loguru sinks for Synapse.

All modules import `from loguru import logger` directly. This module is
called once at process start to wire stderr + per-component file sinks
and to scrub sensitive fields out of any record that accidentally contains them.
"""

from __future__ import annotations

import sys
from typing import Any

from loguru import logger

from synapse.config import get_settings

# Field names that, if present in a log record's extra dict, must be redacted.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "access_token",
        "refresh_token",
        "api_key",
        "token",
        "secret",
        "password",
        "bot_token",
        "telegram_bot_token",
        "anthropic_api_key",
        "openai_api_key",
        "synapse_secret_key",
        "synapse_email_webhook_secret",
        "synapse_browser_api_key",
        "authorization",
    }
)

_REDACTED = "***REDACTED***"


def _scrub_record(record: dict[str, Any]) -> None:
    """Replace sensitive values in record['extra'] in place."""
    extra = record.get("extra", {})
    for key in list(extra.keys()):
        if key.lower() in SENSITIVE_KEYS:
            extra[key] = _REDACTED


def configure_logging(component: str = "synapse") -> None:
    """Wire loguru with stderr + file sinks.

    Args:
        component: Logical component name; controls the per-component log filename.
    """
    settings = get_settings()
    level = settings.synapse_log_level

    logger.remove()

    # Console
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        backtrace=False,
        diagnose=False,  # diagnose=True can leak variable values into logs
        enqueue=False,
    )

    # File sink — only if vault paths exist (i.e., post-init). Skip silently otherwise.
    # enqueue=False because some library exceptions (anthropic.APIStatusError) cannot
    # round-trip through the multiprocessing pickle path that enqueue=True uses.
    # Single-process performance is plenty at personal scale.
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            settings.log_dir / f"{component}.log",
            level=level,
            rotation="10 MB",
            retention="14 days",
            compression="zip",
            backtrace=False,
            diagnose=False,
            enqueue=False,
        )
    except OSError:
        # Vault not initialized yet (e.g., during `synapse init` itself). OK.
        pass

    # Patch every record to scrub sensitive extras.
    logger.configure(patcher=_scrub_record)
