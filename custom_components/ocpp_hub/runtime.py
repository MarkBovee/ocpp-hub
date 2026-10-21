"""What runs for one config entry: the central system, the optional vendor mirror and the bridge to the entities."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN, charger_notification_id, new_charger_signal, snapshot_signal
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
    def __init__(self, hass: HomeAssistant, entry_id: str, build: str, mirror_enabled: bool = False) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._build = build
        self._mirror_enabled = mirror_enabled
        self._announced: set[str] = set()
        # The chargers that already got a notification for their current connection.
        self._notified: set[str] = set()
        self.snapshots: dict[str, ChargerSnapshot] = {}

    @property
    def announced(self) -> list[str]:
        """The chargers that already have entities, or are about to."""
        return sorted(self._announced)

    # A charger connected; it only gets entities and a notification once it has booted.
    def on_connected(self, session: ChargerSession) -> None:
        self._notified.discard(session.cp_id)
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
        if snapshot.cp_id not in self._notified and snapshot.connected and _has_booted(snapshot):
            self._notified.add(snapshot.cp_id)
            self._hass.async_create_task(self._notify_connected(snapshot))
        async_dispatcher_send(self._hass, snapshot_signal(self._entry_id, snapshot.cp_id), snapshot)

    # Tell the owner that a charger connected: who it is and what it speaks, in the language of the instance.
    # The notification has a fixed id per charger, so a reconnect updates it instead of piling up new ones.
    async def _notify_connected(self, snapshot: ChargerSnapshot) -> None:
        texts = await async_get_translations(self._hass, self._hass.config.language, "exceptions", [DOMAIN])

        # Look up one text by its key; the English text is the fallback Home Assistant already merged in.
        def text(key: str) -> str:
            return texts.get(f"component.{DOMAIN}.exceptions.{key}.message", key)

        info = snapshot.info
        message = text("charger_connected").format(
            vendor=info.vendor or "-",
            model=info.model or "-",
            firmware=info.firmware or "-",
            serial=info.serial or snapshot.cp_id,
            protocol=snapshot.protocol or "-",
            mirror=text("charger_connected_mirror_on" if self._mirror_enabled else "charger_connected_mirror_off"),
        )
        persistent_notification.async_create(
            self._hass,
            message,
            title=text("charger_connected_title"),
            notification_id=charger_notification_id(snapshot.cp_id),
        )

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
