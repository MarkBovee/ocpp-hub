"""What runs for one config entry: the central system, the optional vendor mirror and the bridge to the entities."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import new_charger_signal, snapshot_signal
from .core.central import CentralSystem
from .core.frames import Frame
from .core.mirror import VendorMirror
from .core.model import ChargerSnapshot
from .core.session import ChargerSession

_LOGGER = logging.getLogger(__name__)


# True once the charger has said who it is (its BootNotification arrived).
# Entities are created from then on, so the device carries the model and firmware from the start.
def _has_booted(snapshot: ChargerSnapshot) -> bool:
    return snapshot.info.vendor is not None or snapshot.info.model is not None


class HubListener:
    """Hands what the central system reports to Home Assistant: new chargers and every new snapshot."""

    # Keep what is needed to send signals; nothing is sent before the first charger connects.
    def __init__(self, hass: HomeAssistant, entry_id: str, build: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._build = build
        self._announced: set[str] = set()
        self.snapshots: dict[str, ChargerSnapshot] = {}

    @property
    def announced(self) -> list[str]:
        """The chargers that already have entities, or are about to."""
        return sorted(self._announced)

    # A charger connected; it only gets entities once it has booted.
    def on_connected(self, session: ChargerSession) -> None:
        _LOGGER.info("[build %s] Charger %s connected", self._build, session.cp_id)

    # Remember the newest snapshot and pass it on. The first snapshot after the boot announces the charger, before the
    # snapshot itself, so its entities exist and start from the stored state.
    def on_snapshot(self, snapshot: ChargerSnapshot) -> None:
        self.snapshots[snapshot.cp_id] = snapshot
        if snapshot.cp_id not in self._announced and _has_booted(snapshot):
            self._announced.add(snapshot.cp_id)
            _LOGGER.info(
                "[build %s] Charger %s booted: %s %s, firmware %s",
                self._build,
                snapshot.cp_id,
                snapshot.info.vendor,
                snapshot.info.model,
                snapshot.info.firmware,
            )
            async_dispatcher_send(self._hass, new_charger_signal(self._entry_id), snapshot.cp_id)
        async_dispatcher_send(self._hass, snapshot_signal(self._entry_id, snapshot.cp_id), snapshot)

    # Frames are the mirror's business, not the entities'.
    def on_frame(self, cp_id: str, sender: str, frame: Frame) -> None:
        return None

    # The final snapshot with connected=False already told the entities; the log line is for the owner.
    def on_disconnected(self, cp_id: str) -> None:
        _LOGGER.info("[build %s] Charger %s disconnected", self._build, cp_id)


@dataclass
class HubRuntime:
    """The running parts of one config entry."""

    central: CentralSystem
    listener: HubListener
    mirror: VendorMirror | None


type OcppHubConfigEntry = ConfigEntry[HubRuntime]
