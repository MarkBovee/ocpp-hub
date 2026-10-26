"""The history of finished charges: kept in memory, saved to Home Assistant's storage, never truncated."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .core.model import ChargeSession, LimitMemory
from .core.raedian import PlanWindow

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


LIMITS_STORAGE_KEY = f"{DOMAIN}.limits"


class LimitStore:
    """The limits of each charger (the hub's and the vendor's), so they show again after a restart of Home Assistant."""

    # Keep the storage handle; nothing is read until async_load.
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, LIMITS_STORAGE_KEY)
        self._limits: dict[str, LimitMemory] = {}

    # Read the saved limits. A record that cannot be read is skipped with a warning.
    async def async_load(self) -> dict[str, LimitMemory]:
        stored = await self._store.async_load()
        records = stored.get("limits") if isinstance(stored, dict) else None
        for cp_id, record in records.items() if isinstance(records, dict) else []:
            try:
                self._limits[cp_id] = LimitMemory.from_dict(record)
            except KeyError, TypeError, ValueError, AttributeError:
                _LOGGER.warning("Skipping an unreadable limit record in storage")
        return dict(self._limits)

    # Keep the limits of one charger and schedule a save; an empty memory removes the charger.
    def update(self, cp_id: str, memory: LimitMemory) -> None:
        if memory.empty:
            self._limits.pop(cp_id, None)
        else:
            self._limits[cp_id] = memory
        self._store.async_delay_save(self._data, SAVE_DELAY_S)

    # Save now; used when the entry unloads.
    async def async_flush(self) -> None:
        await self._store.async_save(self._data())

    # The limits as the JSON that is written.
    def _data(self) -> dict[str, Any]:
        return {"limits": {cp_id: memory.to_dict() for cp_id, memory in self._limits.items()}}


PLAN_STORAGE_KEY = f"{DOMAIN}.plan"


class PlanStore:
    """The plan of each charger (windows per weekday), so the planning survives a restart of Home Assistant."""

    # Keep the storage handle; nothing is read until async_load.
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, PLAN_STORAGE_KEY)
        self._plans: dict[str, list[PlanWindow]] = {}

    # Read the saved plans. A record that cannot be read is skipped with a warning.
    async def async_load(self) -> None:
        stored = await self._store.async_load()
        records = stored.get("plans") if isinstance(stored, dict) else None
        for cp_id, windows in records.items() if isinstance(records, dict) else []:
            try:
                self._plans[cp_id] = [PlanWindow(int(w[0]), int(w[1]), int(w[2])) for w in windows]
            except KeyError, TypeError, ValueError, IndexError:
                _LOGGER.warning("Skipping an unreadable plan in storage")

    # The plan of a charger, or None when the hub has none yet.
    def get(self, cp_id: str) -> list[PlanWindow] | None:
        return list(self._plans[cp_id]) if cp_id in self._plans else None

    # Keep the plan of a charger and schedule a save.
    def set(self, cp_id: str, windows: list[PlanWindow]) -> None:
        self._plans[cp_id] = list(windows)
        self._store.async_delay_save(self._data, SAVE_DELAY_S)

    # Save now; used when the entry unloads.
    async def async_flush(self) -> None:
        await self._store.async_save(self._data())

    # The plans as the JSON that is written.
    def _data(self) -> dict[str, Any]:
        return {
            "plans": {cp: [[w.weekday, w.start_s, w.end_s] for w in windows] for cp, windows in self._plans.items()}
        }
