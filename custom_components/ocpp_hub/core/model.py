"""Immutable data shapes describing a charger as the hub sees it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

# Key used for a measurement that has no phase (the total, or a single-phase charger).
PHASE_TOTAL = "total"


@dataclass(frozen=True, slots=True)
class ChargerInfo:
    """Identity reported in BootNotification."""

    vendor: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None


@dataclass(frozen=True, slots=True)
class MeterSample:
    """One reading of the charger's meter. Units are fixed: watt, watt-hour, ampere, volt."""

    sampled_at: datetime
    power_w: float | None = None
    energy_wh: float | None = None
    current_a: Mapping[str, float] = field(default_factory=dict)
    voltage_v: Mapping[str, float] = field(default_factory=dict)
    current_offered_a: float | None = None


@dataclass(frozen=True, slots=True)
class ConnectorState:
    """Status of one connector, plus the transaction running on it."""

    connector_id: int
    status: str = "Unavailable"
    error_code: str = "NoError"
    info: str | None = None
    transaction_id: int | None = None
    id_tag: str | None = None
    meter_start_wh: float | None = None


@dataclass(frozen=True, slots=True)
class ChargerSnapshot:
    """Everything the Home Assistant layer needs to show about one charger."""

    cp_id: str
    connected: bool = False
    protocol: str | None = None
    info: ChargerInfo = field(default_factory=ChargerInfo)
    features: frozenset[str] = frozenset()
    configuration: Mapping[str, str] = field(default_factory=dict)
    connectors: Mapping[int, ConnectorState] = field(default_factory=dict)
    meter: MeterSample | None = None
    last_seen: datetime | None = None
