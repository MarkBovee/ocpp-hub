"""Sensors: what the charger reports, and how the link to the vendor backend is doing."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import MIRROR_POLL_INTERVAL, session_signal
from .core import readings
from .core.mirror_model import MirrorStatus
from .core.model import ChargerSnapshot
from .entity import OcppHubEntity, async_add_charger_entities
from .runtime import HubRuntime, OcppHubConfigEntry

# The mirror pushes no events, so the entities that show it are polled; a status ten seconds old is fresh enough.
# Only the mirror entities poll, the charger sensors are pushed.
SCAN_INTERVAL = MIRROR_POLL_INTERVAL

PHASES = ("L1", "L2", "L3")
MIRROR_STATES = ["idle", "connecting", "connected"]


@dataclass(frozen=True, kw_only=True)
class ChargerSensorDescription(SensorEntityDescription):
    """A sensor whose value is read from the charger snapshot."""

    value_fn: Callable[[ChargerSnapshot], StateType]


# The charger's own values. Voltage is off by default: most installations only need the currents.
CHARGER_SENSORS: tuple[ChargerSensorDescription, ...] = (
    ChargerSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=readings.CONNECTOR_STATUS_OPTIONS,
        value_fn=readings.connector_status,
    ),
    ChargerSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=readings.power_w,
    ),
    ChargerSensorDescription(
        key="energy",
        translation_key="energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=readings.energy_kwh,
    ),
    *(
        ChargerSensorDescription(
            key=f"current_{phase.lower()}",
            translation_key="current_phase",
            translation_placeholders={"phase": phase},
            device_class=SensorDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda snapshot, phase=phase: readings.phase_current_a(snapshot, phase),
        )
        for phase in PHASES
    ),
    *(
        ChargerSensorDescription(
            key=f"voltage_{phase.lower()}",
            translation_key="voltage_phase",
            translation_placeholders={"phase": phase},
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
            entity_registry_enabled_default=False,
            value_fn=lambda snapshot, phase=phase: readings.phase_voltage_v(snapshot, phase),
        )
        for phase in PHASES
    ),
    ChargerSensorDescription(
        key="current_offered",
        translation_key="current_offered",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=readings.current_offered_a,
    ),
    ChargerSensorDescription(
        key="session_energy",
        translation_key="session_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=readings.session_energy_kwh,
    ),
    ChargerSensorDescription(
        key="session_started",
        translation_key="session_started",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=readings.session_started,
    ),
    ChargerSensorDescription(
        key="session_duration",
        translation_key="session_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=readings.session_duration_s,
    ),
    ChargerSensorDescription(
        key="model",
        translation_key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.info.model,
    ),
    ChargerSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.info.firmware,
    ),
)


# Create the sensors of every charger that boots, plus the mirror sensors when a vendor mirror is set up.
async def async_setup_entry(
    hass: HomeAssistant, entry: OcppHubConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime = entry.runtime_data

    # The sensors of one charger.
    def build(cp_id: str) -> list[OcppHubEntity]:
        entities: list[OcppHubEntity] = [
            ChargerSensor(runtime, entry.entry_id, cp_id, description) for description in CHARGER_SENSORS
        ]
        entities.append(LastSessionSensor(runtime, entry.entry_id, cp_id))
        if runtime.mirror is not None:
            entities += [
                MirrorStatusSensor(runtime, entry.entry_id, cp_id),
                MirrorQueueSensor(runtime, entry.entry_id, cp_id),
            ]
        return entities

    async_add_charger_entities(hass, entry, async_add_entities, build)


class ChargerSensor(OcppHubEntity, SensorEntity):
    """One value of the charger. It is unavailable while the charger is not connected."""

    entity_description: ChargerSensorDescription

    # Build the sensor for one charger from its description.
    def __init__(self, runtime: HubRuntime, entry_id: str, cp_id: str, description: ChargerSensorDescription) -> None:
        super().__init__(runtime, entry_id, cp_id, "sensor", description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """The value from the latest snapshot."""
        return self.entity_description.value_fn(self._snapshot)

    @property
    def available(self) -> bool:
        """A charger that is gone has no current value."""
        return self._snapshot.connected


# How many finished charges the last-session sensor lists in its attributes. The full history is in the store and the
# get_sessions service; an attribute must stay small, because Home Assistant limits its size.
RECENT_SESSIONS = 20


class LastSessionSensor(OcppHubEntity, SensorEntity):
    """The most recent finished charge: when it ended, with the numbers as attributes and the newest ones listed."""

    _attr_translation_key = "last_session"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    # The list changes with every charge and is large; keeping it out of the recorder keeps the database small.
    _unrecorded_attributes = frozenset({"recent_sessions"})

    # Build the sensor for one charger.
    def __init__(self, runtime: HubRuntime, entry_id: str, cp_id: str) -> None:
        super().__init__(runtime, entry_id, cp_id, "sensor", "last_session")

    # Also follow the history: a charge that finishes changes this sensor, not only the snapshot.
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, session_signal(self._entry_id, self._cp_id), self._history_changed)
        )

    # Show the new history.
    @callback
    def _history_changed(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """The history stays readable while the charger is away."""
        return True

    @property
    def native_value(self) -> datetime | None:
        """When the latest charge ended, or None while there is none."""
        latest = self._runtime.sessions.newest_first(self._cp_id, 1)
        return latest[0].ended_at if latest else None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """The latest charge in plain numbers, the count, and the newest charges for a dashboard table."""
        recent = self._runtime.sessions.newest_first(self._cp_id, RECENT_SESSIONS)
        attributes: dict[str, Any] = {"session_count": self._runtime.sessions.count(self._cp_id)}
        if recent:
            latest = recent[0]
            attributes |= {
                "energy_kwh": round(latest.energy_wh / 1000, 3) if latest.energy_wh is not None else None,
                "duration_s": int(latest.duration_s),
                "started_at": latest.started_at.isoformat(),
                "id_tag": latest.id_tag,
                "max_power_w": latest.max_power_w,
                "stop_reason": latest.stop_reason,
            }
        attributes["recent_sessions"] = [
            {
                "started_at": s.started_at.isoformat(),
                "ended_at": s.ended_at.isoformat(),
                "energy_kwh": round(s.energy_wh / 1000, 3) if s.energy_wh is not None else None,
                "duration_s": int(s.duration_s),
                "max_power_w": s.max_power_w,
                "id_tag": s.id_tag,
                "stop_reason": s.stop_reason,
            }
            for s in recent
        ]
        return attributes


class MirrorEntity(OcppHubEntity):
    """Base of the mirror sensors: they read the live status of the charger's mirror link."""

    _attr_should_poll = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    # The status of this charger's link, read live.
    def _status(self) -> MirrorStatus | None:
        """The mirror status of this charger, or None when it has no link right now."""
        assert self._runtime.mirror is not None
        return self._runtime.mirror.status(self._cp_id)


class MirrorStatusSensor(MirrorEntity, SensorEntity):
    """Whether the link to the vendor backend is up, with the last error and counters as attributes."""

    _attr_translation_key = "mirror_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = MIRROR_STATES

    # Build the sensor for one charger.
    def __init__(self, runtime: HubRuntime, entry_id: str, cp_id: str) -> None:
        super().__init__(runtime, entry_id, cp_id, "sensor", "mirror_status")

    @property
    def native_value(self) -> str:
        """connected, connecting (no link yet or the vendor is not reachable) or idle (the charger is not here)."""
        status = self._status()
        if status is None:
            return "idle"
        return "connected" if status.connected else "connecting"

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """The last error and success and the counters, for the owner who wants to know why."""
        status = self._status()
        if status is None:
            return {}
        return {
            "last_error": status.last_error,
            "last_success": status.last_success.isoformat() if status.last_success else None,
            "dropped_frames": status.dropped_frames,
            "unmapped_transactions": status.unmapped_transactions,
        }


class MirrorQueueSensor(MirrorEntity, SensorEntity):
    """How many frames wait for the vendor backend."""

    _attr_translation_key = "mirror_queue"
    _attr_state_class = SensorStateClass.MEASUREMENT

    # Build the sensor for one charger.
    def __init__(self, runtime: HubRuntime, entry_id: str, cp_id: str) -> None:
        super().__init__(runtime, entry_id, cp_id, "sensor", "mirror_queue")

    @property
    def native_value(self) -> int:
        """The number of frames waiting; zero when there is no link."""
        status = self._status()
        return status.queue_length if status is not None else 0
