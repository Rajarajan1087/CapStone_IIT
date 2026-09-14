"""
Central configuration for the CloudServe support system.

Every tunable value the pipeline depends on is read here, once, from the
environment. Nothing else in the codebase reads os.environ directly, so a
reader can see the full set of knobs in one place and a misconfiguration
fails loudly at startup rather than silently three stages later.

Values come from a .env file in the project root (see .env.example). The
loader falls back to a small stdlib parser if python-dotenv is not
installed, so the module never becomes the reason the system will not
start.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """
    Load .env into os.environ.

    Prefers python-dotenv when available. The stdlib fallback handles the
    subset of .env syntax this project uses -- KEY=value, # comments, blank
    lines, and optional surrounding quotes -- so that a missing dependency
    degrades to reduced convenience rather than a crash.
    """
    env_path = PROJECT_ROOT / ".env"
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_path)
        return
    except ImportError:
        pass

    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Do not clobber a variable already set in the real environment;
        # an explicitly exported value should win over the file.
        os.environ.setdefault(key, value)


_load_dotenv()


def _get_str(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    # A key pasted from a browser frequently carries a trailing newline or
    # space, which produces a 401 that looks like a bad key rather than a
    # bad copy-paste. Strip it here, once, for every string setting.
    return value.strip()


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(
            f"{name} must be a number, got {raw!r}. Check your .env file."
        ) from None


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"{name} must be a whole number, got {raw!r}. Check your .env file."
        ) from None


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of configuration, resolved once at import time."""

    # --- Model access -------------------------------------------------
    # Two providers are supported. The pipeline is deliberately not
    # coupled to either: the client exposes one contract and the provider
    # is a configuration value, so a provider outage or a change of free
    # tier is a one-line change rather than a rewrite.
    model_provider: str            # "openrouter" or "mistral"
    openrouter_api_key: str
    mistral_api_key: str
    model_name: str
    embedding_model: str

    # --- Storage paths ------------------------------------------------
    chroma_path: Path
    database_url: str

    # --- Pipeline behaviour -------------------------------------------
    # Confidence at or above which the system may answer automatically.
    # The value shipped here is a starting point only; the value actually
    # used is derived from the development set during B-07 and recorded in
    # the report with its justification (PRD open question 2).
    confidence_threshold: float
    # Passages scoring below this are discarded rather than returned.
    # Returning nothing is a valid and often correct outcome (FR-03).
    retrieval_score_threshold: float
    retrieval_top_k: int

    # --- Resilience (A11) ---------------------------------------------
    request_timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    # Minimum gap between live model calls, in seconds. Free tiers publish
    # a sustained rate rather than a burst allowance, so pacing under that
    # rate avoids the 429 entirely instead of paying retries to discover
    # it. 1.1s sits just under Mistral's published 1 request per second.
    # Set to 0 to disable pacing.
    min_request_interval_seconds: float
    # When true, model responses are cached on disk by prompt hash. This
    # saves free-tier allowance, makes runs reproducible, and means a
    # re-run after a crash does not pay for work already done.
    cache_enabled: bool
    cache_path: Path

    log_level: str

    @property
    def active_api_key(self) -> str:
        """The key belonging to the configured provider."""
        if self.model_provider == "mistral":
            return self.mistral_api_key
        return self.openrouter_api_key

    @property
    def has_model_access(self) -> bool:
        """
        True when a usable-looking key is configured for the active provider.

        The pipeline consults this to choose between the live model path
        and the degraded rule-only path, so that the absence of a key is a
        documented operating mode rather than a crash.
        """
        key = self.active_api_key
        return bool(key) and key not in ("your_key_here", "your_mistral_key_here")


def _resolve_path(raw: str) -> Path:
    """
    Resolve a configured path to an absolute one, anchored at the project root.

    Chroma silently returns zero results when the store is written to one
    directory and read from another, which is one of the most commonly
    reported setup failures. Anchoring every path here means both the
    writer and the reader get the identical absolute path regardless of
    the directory the process was launched from.
    """
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


_provider = _get_str("MODEL_PROVIDER", "openrouter").lower()
if _provider not in ("openrouter", "mistral"):
    raise ValueError(
        f"MODEL_PROVIDER must be 'openrouter' or 'mistral', got {_provider!r}. "
        f"Check your .env file."
    )

# Each provider names its models differently, so the default follows the
# provider rather than forcing the user to set both values together.
_default_model = ("mistral-small-latest" if _provider == "mistral"
                  else "meta-llama/llama-3.1-8b-instruct")

settings = Settings(
    model_provider=_provider,
    openrouter_api_key=_get_str("OPENROUTER_API_KEY", ""),
    mistral_api_key=_get_str("MISTRAL_API_KEY", ""),
    model_name=_get_str("MODEL_NAME", _default_model),
    embedding_model=_get_str("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
    chroma_path=_resolve_path(_get_str("CHROMA_PATH", "./storage/chroma")),
    database_url=_get_str("DATABASE_URL", "sqlite:///./storage/decisions.db"),
    confidence_threshold=_get_float("CONFIDENCE_THRESHOLD", 0.45),
    retrieval_score_threshold=_get_float("RETRIEVAL_SCORE_THRESHOLD", 0.35),
    retrieval_top_k=_get_int("RETRIEVAL_TOP_K", 5),
    request_timeout_seconds=_get_float("REQUEST_TIMEOUT_SECONDS", 30.0),
    max_retries=_get_int("MAX_RETRIES", 4),
    retry_backoff_seconds=_get_float("RETRY_BACKOFF_SECONDS", 2.0),
    min_request_interval_seconds=_get_float("MIN_REQUEST_INTERVAL_SECONDS", 1.1),
    cache_enabled=_get_str("CACHE_ENABLED", "true").lower() in ("1", "true", "yes"),
    cache_path=_resolve_path(_get_str("CACHE_PATH", "./storage/model_cache")),
    log_level=_get_str("LOG_LEVEL", "INFO").upper(),
)


def sqlite_path_from_url(url: str) -> Path:
    """
    Extract a filesystem path from a sqlite:/// URL.

    The decision log uses sqlite3 directly rather than SQLAlchemy, so the
    DATABASE_URL from the pack's .env.example is translated here instead of
    being parsed at each call site.
    """
    prefix = "sqlite:///"
    if url.startswith(prefix):
        return _resolve_path(url[len(prefix):])
    # A non-sqlite URL is out of scope for this project; fail clearly
    # rather than silently writing to an unexpected location.
    raise ValueError(
        f"Only sqlite:/// DATABASE_URLs are supported by this project, got {url!r}"
    )
