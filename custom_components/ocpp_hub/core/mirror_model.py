"""Settings and status shapes of the vendor mirror."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from .frames import basic_auth_header

# The only command policy so far: whatever the vendor backend asks of the charger is refused.
COMMANDS_REJECT = "reject"


@dataclass(frozen=True, slots=True)
class MirrorSettings:
    """How the mirror reaches the vendor backend and how patient it is."""

    url: str
    user: str | None = None
    # Kept out of repr so a log line or a diagnostics dump of the settings can never show it.
    password: str | None = field(default=None, repr=False)
    commands: str = COMMANDS_REJECT
    max_queue: int = 5000
    reply_timeout_s: float = 30.0
    connect_timeout_s: float = 10.0
    backoff_min_s: float = 1.0
    backoff_max_s: float = 30.0
    # Used until the vendor has told its own heartbeat interval in the answer to BootNotification.
    fallback_heartbeat_s: int = 300

    # Reject settings that cannot work, at the boundary, so a typo shows up as an error and not as a silent retry loop.
    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        if parts.scheme not in ("ws", "wss") or not parts.hostname:
            raise ValueError("the vendor URL must start with ws:// or wss:// and name a host")
        if parts.username is not None or parts.password is not None:
            raise ValueError("the vendor URL must not contain credentials; use the user and password settings")
        if bool(self.user) != bool(self.password):
            raise ValueError("set both the vendor user and password, or neither")
        if self.commands != COMMANDS_REJECT:
            raise ValueError(f"unsupported command policy {self.commands!r}; only {COMMANDS_REJECT!r} exists")
        if self.max_queue < 1 or self.fallback_heartbeat_s < 1:
            raise ValueError("the queue size and the fallback heartbeat interval must be at least 1")
        if min(self.reply_timeout_s, self.connect_timeout_s, self.backoff_min_s, self.backoff_max_s) <= 0:
            raise ValueError("timeouts and backoff delays must be positive")
        if self.backoff_min_s > self.backoff_max_s:
            raise ValueError("the minimum backoff delay cannot exceed the maximum")

    @property
    def use_tls(self) -> bool:
        """True for a wss:// URL, which needs an SSL context."""
        return urlsplit(self.url).scheme == "wss"

    @property
    def auth_header(self) -> tuple[str, str] | None:
        """The Basic authorization header for the websocket upgrade, or None when no credentials are set."""
        if not self.user or not self.password:
            return None
        return basic_auth_header(self.user, self.password)


@dataclass(frozen=True, slots=True)
class MirrorStatus:
    """What Home Assistant shows about the mirror link of one charger."""

    connected: bool
    queue_length: int
    last_success: datetime | None = None
    last_error: str | None = None
    # Frames thrown away because the queue was full (only meter values and status changes are ever dropped).
    dropped_frames: int = 0
    # Stop or meter frames whose transaction the vendor never confirmed, so no vendor id could be given.
    unmapped_transactions: int = 0
