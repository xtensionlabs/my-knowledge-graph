"""Central configuration for Synapse.

All constants, paths, thresholds, and tunables live here. No magic numbers
elsewhere in the codebase. Environment values are loaded via pydantic-settings
from `.env` at the repo root.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Capture contract ──────────────────────────────────────────────────────────
INBOX_FRONTMATTER_KEYS: tuple[str, ...] = (
    "id",
    "source",
    "captured_at",
    "raw",
    "processed",
)

VALID_CAPTURE_SOURCES: tuple[str, ...] = (
    "telegram",
    "voice",
    "clipboard",
    "git",
    "email",
    "browser",
    "ocr",
    "image-pending-ocr",
    "manual",
)

# ── Clipboard daemon thresholds ──────────────────────────────────────────────
CLIPBOARD_POLL_INTERVAL_SECONDS: float = 3.0
CLIPBOARD_MIN_LENGTH: int = 60
CLIPBOARD_DEDUP_WINDOW: int = 50
# Regexes that indicate likely credential / sensitive content — skipped silently.
CLIPBOARD_SKIP_PATTERNS: tuple[str, ...] = (
    r"^[A-Za-z0-9+/=]{40,}$",                       # base64-ish blob
    r"^[a-f0-9]{32,}$",                             # long hex token
    r"^[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}$",  # JWT
    r"(?i)password\s*[:=]",                         # explicit password
    r"(?i)api[_-]?key\s*[:=]",
    r"(?i)secret\s*[:=]",
    r"(?i)token\s*[:=]",
    r"-----BEGIN [A-Z ]+ PRIVATE KEY-----",
    r"^xox[bpoa]-[A-Za-z0-9-]+$",                   # Slack token
    r"^ghp_[A-Za-z0-9]{36}$",                       # GitHub PAT
    r"^sk-[A-Za-z0-9]{20,}$",                       # OpenAI key shape
)

# ── Telegram bot retry queue ─────────────────────────────────────────────────
TELEGRAM_RETRY_INTERVAL_SECONDS: int = 60
TELEGRAM_REPLY_ACK: str = "✓"

# ── Telegram HTTPXRequest timeouts ───────────────────────────────────────────
# Generous values for variable Nairobi internet. Default SDK timeouts (5s) cause
# spurious failures and break the zero-capture-loss promise at the network layer.
# All seconds. See `feedback-network-timeouts` memory for rationale.
TELEGRAM_CONNECT_TIMEOUT_SECONDS: float = 90.0
TELEGRAM_READ_TIMEOUT_SECONDS: float = 600.0   # long-poll friendly
TELEGRAM_WRITE_TIMEOUT_SECONDS: float = 120.0
TELEGRAM_POOL_TIMEOUT_SECONDS: float = 300.0

# ── Email ingest ─────────────────────────────────────────────────────────────
EMAIL_HMAC_HEADER: str = "x-synapse-signature"
EMAIL_MAX_BODY_BYTES: int = 25 * 1024 * 1024  # 25 MB

# ── Browser ingest ───────────────────────────────────────────────────────────
BROWSER_API_KEY_HEADER: str = "x-synapse-api-key"

# ── Gateway retry / timeouts ─────────────────────────────────────────────────
HTTP_MAX_RETRIES: int = 3
HTTP_INITIAL_BACKOFF_SECONDS: float = 1.0
HTTP_BACKOFF_FACTOR: float = 2.0

# ── Claude model tiers (per `model-tiers` memory + PRD §7) ───────────────────
# Sonnet for high-volume mechanical agents; Opus for user-facing reasoning.
LIBRARIAN_MODEL: str = "claude-sonnet-4-5"
SCOUT_MODEL: str = "claude-sonnet-4-5"
GUARDIAN_MODEL: str = "claude-haiku-4-5"   # downgraded from Sonnet 2026-05-26
SYNTHESIZER_MODEL: str = "claude-opus-4-7"
CRITIC_MODEL: str = "claude-opus-4-7"
STRATEGIST_MODEL: str = "claude-opus-4-7"

# Vision-capable Sonnet for OCR-with-meaning (M5 diagram conversion).
# Pure-text OCR still goes Tesseract local (free).
VISION_MODEL: str = "claude-sonnet-4-5"

# Per-1M-token prices in USD (input, output). Used for the api_usage cost field.
# Update when Anthropic changes pricing — keep this single table the source of truth.
MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5":   (3.00, 15.00),
    "claude-sonnet-4-6":   (3.00, 15.00),
    "claude-opus-4-7":    (15.00, 75.00),
    "claude-haiku-4-5":    (1.00,  5.00),
}

# Models that reject the `temperature` parameter (newer reasoning models manage
# sampling internally). The LLM client skips the field for these.
MODELS_WITHOUT_TEMPERATURE: frozenset[str] = frozenset({
    "claude-opus-4-7",
})

# ── Claude SDK timeouts (per `feedback-network-timeouts`) ────────────────────
ANTHROPIC_CONNECT_TIMEOUT_SECONDS: float = 30.0
ANTHROPIC_READ_TIMEOUT_SECONDS: float = 300.0
ANTHROPIC_MAX_TOKENS: int = 4096  # default output cap; agents may override

# ── Embeddings (local, no API spend per `feedback-claude-first`) ────────────
EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION: int = 384  # all-MiniLM-L6-v2

# ── ChromaDB ─────────────────────────────────────────────────────────────────
CHROMA_SUBDIR: str = "chroma"
CHROMA_COLLECTION_NAME: str = "synapse_nodes"

# ── Prompts ──────────────────────────────────────────────────────────────────
# Resolved at runtime relative to the synapse package, NOT the vault.
PROMPTS_PACKAGE_SUBDIR: str = "prompts"

# ── Librarian thresholds (PRD §7.1) ──────────────────────────────────────────
LIBRARIAN_CONFIDENCE_THRESHOLD: float = 0.6   # below → needs_review
LIBRARIAN_MAX_ITEMS_PER_RUN: int = 100        # safety cap on a single sweep
LIBRARIAN_PENDING_REVIEW_FILE: str = "pending_review.md"
LIBRARIAN_PENDING_INSIGHTS_FILE: str = "pending_insights.md"

# ── Search ranking ───────────────────────────────────────────────────────────
SEARCH_DEFAULT_LIMIT: int = 10
SEARCH_CENTRALITY_BOOST: float = 0.2  # weight of graph-centrality vs semantic distance

# ── M2: Synthesizer + scheduling ─────────────────────────────────────────────
# User confirmed Africa/Nairobi (EAT, UTC+3) on 2026-05-23 for the Delta Briefing.
SYNAPSE_TIMEZONE: str = "Africa/Nairobi"
DELTA_BRIEFING_HOUR: int = 7              # 07:00 local
DELTA_BRIEFING_MINUTE: int = 0
LIBRARIAN_SCHEDULE_INTERVAL_HOURS: int = 2  # PRD §7.1
ENERGY_REFRESH_INTERVAL_MINUTES: int = 30   # PRD §6.2
GUARDIAN_SCHEDULE_INTERVAL_HOURS: int = 4   # PRD §7.6 (M4; constant lives here now)

# Horizon queue (PRD §6.3)
HORIZON_LOOKAHEAD_HOURS: int = 72       # what counts as "upcoming"
HORIZON_PRELOAD_HOURS: int = 48         # when to accelerate review of linked CONCEPTs
HORIZON_ACCELERATED_NEXT_REVIEW_HOURS: int = 24  # cap next_review to ≤ this

# Synthesizer thresholds
SYNTHESIZER_RETENTION_ALERTS: int = 3     # PRD §7.2 "3 CONCEPT nodes ..."
SYNTHESIZER_QUESTION_BANK_MAX: int = 5    # rolling bank size per concept
SYNTHESIZER_DAILY_FILE_FORMAT: str = "%Y-%m-%d.md"
SYNTHESIZER_OPEN_QUESTION_AGE_DAYS: int = 3  # surface QUESTIONs older than this

# ── M3: Git ingest + manifest ────────────────────────────────────────────────
SYNAPSE_MANIFEST_FILENAME: str = "synapse.json"
GIT_HOOK_SCRIPT_NAME: str = "post-commit"
# Default gateway URL embedded in the hook script; overridable via `--gateway`.
GIT_HOOK_DEFAULT_GATEWAY: str = "http://127.0.0.1:8000"

# ── Vault subdirectories (created by `synapse init`) ─────────────────────────
VAULT_SUBDIRS: tuple[str, ...] = (
    "inbox",
    "concepts",
    "concepts/calculus",
    "concepts/discrete-math",
    "concepts/cs-fundamentals",
    "builds",
    "people",
    "events",
    "questions",
    "insights",
    "bridge",
    "courses",
    "courses/ICS1103-differential-calculus",
    "courses/ICS1104-discrete-mathematics",
    "xtension",
    "xtension/signal",
    "xtension/strategy",
    "xtension/architecture",
    "daily",
    "scout",
    "archive",
    "attachments",
    "attachments/voice",
    "attachments/images",
    "attachments/docs",
)

# Internal Synapse runtime directory (DB, PID files, logs) lives inside the vault.
VAULT_INTERNAL_DIR: str = ".synapse"
VAULT_DB_FILENAME: str = "synapse.db"
VAULT_PID_DIR: str = "run"
VAULT_LOG_DIR: str = "logs"


class Settings(BaseSettings):
    """Environment-backed runtime settings.

    Loaded once at startup; cached via `get_settings()`. Sensitive fields
    (tokens, keys) are typed as strings and never logged.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    # Core
    synapse_secret_key: str = Field(default="", description="Fernet master key.")
    synapse_vault_path: Path = Field(
        default=Path("./SYNAPSE").resolve(),
        description="Absolute path to the Obsidian vault root.",
    )
    synapse_gateway_host: str = "127.0.0.1"
    synapse_gateway_port: int = 8000
    synapse_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # LLM (used from M1 onward)
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Capture
    telegram_bot_token: str = ""
    telegram_allowed_user_id: int | None = None
    synapse_email_webhook_secret: str = ""
    synapse_browser_api_key: str = ""

    # Integrations (used from M4 onward)
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""

    # Vision (M5)
    google_cloud_vision_api_key: str = ""

    @field_validator("synapse_vault_path", mode="before")
    @classmethod
    def _resolve_vault(cls, v: object) -> Path:
        """Expand ~, resolve to absolute path."""
        if v is None or v == "":
            return Path("./SYNAPSE").resolve()
        return Path(str(v)).expanduser().resolve()

    @field_validator("telegram_allowed_user_id", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        """Treat blank `.env` values as None for optional int fields."""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    # Convenience derived paths --------------------------------------------------

    @property
    def vault_internal_dir(self) -> Path:
        """`${vault}/.synapse/` — runtime files (DB, PIDs, logs)."""
        return self.synapse_vault_path / VAULT_INTERNAL_DIR

    @property
    def db_path(self) -> Path:
        """Absolute path to the SQLite database file."""
        return self.vault_internal_dir / VAULT_DB_FILENAME

    @property
    def db_url(self) -> str:
        """SQLAlchemy URL for the SQLite database."""
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def inbox_dir(self) -> Path:
        return self.synapse_vault_path / "inbox"

    @property
    def archive_dir(self) -> Path:
        return self.synapse_vault_path / "archive"

    @property
    def attachments_dir(self) -> Path:
        return self.synapse_vault_path / "attachments"

    @property
    def pid_dir(self) -> Path:
        return self.vault_internal_dir / VAULT_PID_DIR

    @property
    def log_dir(self) -> Path:
        return self.vault_internal_dir / VAULT_LOG_DIR

    @property
    def chroma_dir(self) -> Path:
        """Absolute path to the ChromaDB on-disk store."""
        return self.vault_internal_dir / CHROMA_SUBDIR

    @property
    def prompts_dir(self) -> Path:
        """Absolute path to `synapse/prompts/` (packaged with the code, not the vault)."""
        return Path(__file__).resolve().parent / PROMPTS_PACKAGE_SUBDIR


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings instance.

    Returns:
        The process-wide Settings singleton.
    """
    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache. Test-only — never call in production code."""
    get_settings.cache_clear()
