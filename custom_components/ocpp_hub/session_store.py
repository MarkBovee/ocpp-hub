"""The history of finished charges: kept in memory, saved to Home Assistant's storage, never truncated."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .core.model import ChargeSession

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.sessions"
# A save is delayed a few seconds so that a burst of events becomes one write; unload saves at once.
SAVE_DELAY_S = 5.0


class SessionStore:
    """Every finished charge of every charger, oldest first. A charge is about 300 bytes, so no limit is needed."""

    # Keep the storage handle; nothing is read until async_load.
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._sessions: list[ChargeSession] = []

    # Read the saved history. A record that cannot be read is skipped with a warning, so one damaged entry does not
    # cost the owner the rest of the history.
    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        loaded: list[ChargeSession] = []
        for record in stored.get("sessions", []):
            try:
                loaded.append(ChargeSession.from_dict(record))
            except KeyError, TypeError, ValueError:
                _LOGGER.warning("Skipping an unreadable charge record in the session history")
        self._sessions = loaded

    # Add a finished charge and schedule a save.
    def add(self, session: ChargeSession) -> None:
        self._sessions.append(session)
        self._store.async_delay_save(self._data, SAVE_DELAY_S)

    # Save now; used when the entry unloads so that nothing waits in the delay.
    async def async_flush(self) -> None:
        await self._store.async_save(self._data())

    # The history as the JSON that is written.
    def _data(self) -> dict[str, Any]:
        return {"sessions": [session.to_dict() for session in self._sessions]}

    # The charges of one charger, or of all, newest first; `limit` keeps only the newest ones.
    def newest_first(self, cp_id: str | None = None, limit: int | None = None) -> list[ChargeSession]:
        chosen = [s for s in reversed(self._sessions) if cp_id is None or s.cp_id == cp_id]
        return chosen if limit is None else chosen[:limit]

    # How many charges one charger, or all, have in the history.
    def count(self, cp_id: str | None = None) -> int:
        return sum(1 for s in self._sessions if cp_id is None or s.cp_id == cp_id)
