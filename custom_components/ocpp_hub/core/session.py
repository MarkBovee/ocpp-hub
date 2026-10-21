"""One charger connection: answers the charger's requests, tracks its state and sends requests of its own."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from websockets.exceptions import ConnectionClosed

from . import commands
from .frames import (
    ERROR_FORMATION_VIOLATION,
    ERROR_INTERNAL,
    ERROR_NOT_IMPLEMENTED,
    MSG_CALL,
    MSG_CALL_ERROR,
    MSG_CALL_RESULT,
    Frame,
    build_call,
    build_call_error,
    build_call_result,
    parse_frame,
)
from .metering import parse_meter_values
from .model import ChargerInfo, ChargerSnapshot, ConnectorState
from .readings import PRIMARY_CONNECTOR

_LOGGER = logging.getLogger(__name__)

# Who sent a frame, as the listeners see it.
SENDER_CHARGER = "CP"
SENDER_HUB = "CSMS"

# The configuration keys read after a charger boots; asked in one request, the charger answers what it knows.
CONFIG_KEYS = [
    "SupportedFeatureProfiles",
    "MeterValuesSampledData",
    "MeterValueSampleInterval",
    "HeartbeatInterval",
    "NumberOfConnectors",
    "ChargeProfileMaxStackLevel",
    "ChargingScheduleAllowedChargingRateUnit",
]


class Connection(Protocol):
    """The part of a websocket connection a session needs."""

    async def send(self, message: str) -> None: ...

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class SessionListener(Protocol):
    """Receives what happens on the charger connections. The Home Assistant layer and the mirror both implement it."""

    def on_connected(self, session: ChargerSession) -> None: ...

    def on_snapshot(self, snapshot: ChargerSnapshot) -> None: ...

    def on_frame(self, cp_id: str, sender: str, frame: Frame) -> None: ...

    def on_disconnected(self, cp_id: str) -> None: ...


class CallError(Exception):
    """A request the hub sent to the charger did not get a usable answer."""


class CallTimeoutError(CallError):
    """The charger did not answer in time."""


class CallRejectedError(CallError):
    """The charger answered with a CALLERROR."""

    # Keep the code and description the charger gave.
    # Callers log them and tests assert on them.
    def __init__(self, code: str, description: str) -> None:
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


class FormationError(ValueError):
    """A request from the charger is missing a field or has one of the wrong type."""


@dataclass(frozen=True, slots=True)
class SessionSettings:
    """Timing and metering choices of a session."""

    heartbeat_interval_s: int = 300
    call_timeout_s: float = 15.0
    # A charger that has just connected ignores requests for a while (the NEO for about ten seconds, and a first
    # request at five seconds was never answered), so setup waits this long before its first try.
    setup_delay_s: float = 15.0
    # How long setup waits before each further try when the charger did not answer; no more tries after the last one.
    setup_retry_delays_s: tuple[float, ...] = (30.0, 60.0, 120.0, 300.0)
    # A charger that reconnects after the hub restarted often sends no BootNotification and no StatusNotification,
    # because the hub accepted it before. After this wait the hub asks for them itself.
    boot_wait_s: float = 15.0
    # How long the hub lets a requested BootNotification arrive before it judges what is still missing.
    trigger_settle_s: float = 3.0
    meter_sample_interval_s: int = 15
    measurands: str = "Energy.Active.Import.Register,Power.Active.Import,Current.Import,Voltage,Current.Offered"


# Read a required integer field from a payload or raise FormationError.
# A wrong type is the charger's mistake and is answered with FormationViolation instead of crashing the session.
def _int_field(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FormationError(f"{name} must be an integer")
    return value


class ChargerSession:
    """The hub's side of one charger connection."""

    # Keep the connection, listener and settings; nothing is sent until run() is called.
    def __init__(
        self,
        cp_id: str,
        protocol: str,
        connection: Connection,
        listener: SessionListener,
        next_transaction_id: Callable[[], int],
        settings: SessionSettings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._connection = connection
        self._listener = listener
        self._next_transaction_id = next_transaction_id
        self._settings = settings or SessionSettings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._pending: dict[str, asyncio.Future[Frame]] = {}
        self._call_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._snapshot = ChargerSnapshot(cp_id=cp_id, connected=True, protocol=protocol, last_seen=self._clock())
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "BootNotification": self._boot_notification,
            "Heartbeat": self._heartbeat,
            "StatusNotification": self._status_notification,
            "MeterValues": self._meter_values,
            "StartTransaction": self._start_transaction,
            "StopTransaction": self._stop_transaction,
            "Authorize": self._authorize,
            "DataTransfer": self._data_transfer,
            "FirmwareStatusNotification": self._acknowledge,
            "DiagnosticsStatusNotification": self._acknowledge,
        }

    @property
    def cp_id(self) -> str:
        """The identity the charger connected with."""
        return self._snapshot.cp_id

    @property
    def snapshot(self) -> ChargerSnapshot:
        """The latest state of the charger."""
        return self._snapshot

    # Read frames until the connection ends, then mark the charger as gone.
    # The listener is told on every exit path, so a crashed session can never leave a charger shown as connected.
    async def run(self) -> None:
        self._listener.on_connected(self)
        self._listener.on_snapshot(self._snapshot)
        self._start_task(self._ask_for_introduction())
        try:
            async for message in self._connection:
                await self._handle_message(message)
        except ConnectionClosed:
            pass
        finally:
            for task in list(self._tasks):
                task.cancel()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(CallError("connection closed"))
            self._snapshot = replace(self._snapshot, connected=False)
            self._listener.on_snapshot(self._snapshot)
            self._listener.on_disconnected(self.cp_id)

    # Close the connection from the hub's side.
    # Used when the same charger reconnects before the hub noticed the old link dying.
    async def close(self) -> None:
        await self._connection.close()

    # Send a request to the charger and return the payload of its answer.
    # One request at a time per direction is a protocol rule, so calls are serialised with a lock.
    async def call(self, action: str, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        async with self._call_lock:
            unique_id = uuid.uuid4().hex
            future: asyncio.Future[Frame] = asyncio.get_running_loop().create_future()
            self._pending[unique_id] = future
            try:
                await self._send(build_call(unique_id, action, payload))
                frame = await asyncio.wait_for(future, timeout or self._settings.call_timeout_s)
            except TimeoutError:
                raise CallTimeoutError(f"{action} was not answered") from None
            finally:
                self._pending.pop(unique_id, None)
        if frame[0] == MSG_CALL_ERROR:
            raise CallRejectedError(str(frame[2]), str(frame[3]) if len(frame) > 3 else "")
        result = frame[2]
        return result if isinstance(result, dict) else {}

    # Send one frame to the charger and tell the listener about it.
    # Every frame the hub sends passes here, so listeners such as the mirror see the hub's answers too.
    async def _send(self, text: str) -> None:
        frame = parse_frame(text)
        await self._connection.send(text)
        if frame is not None:
            self._listener.on_frame(self.cp_id, SENDER_HUB, frame)

    # Handle one raw message from the charger: tell the listener, then answer a request or resolve a pending call.
    # Garbage is logged and ignored; closing the link over one bad frame would only make the charger reconnect.
    async def _handle_message(self, raw: str | bytes) -> None:
        self._snapshot = replace(self._snapshot, last_seen=self._clock())
        frame = parse_frame(raw)
        if frame is None:
            _LOGGER.warning("Ignoring an unreadable frame from %s", self.cp_id)
            return
        self._listener.on_frame(self.cp_id, SENDER_CHARGER, frame)
        if frame[0] == MSG_CALL:
            await self._handle_call(frame)
        elif frame[0] in (MSG_CALL_RESULT, MSG_CALL_ERROR):
            future = self._pending.get(str(frame[1]))
            if future is not None and not future.done():
                future.set_result(frame)

    # Answer a request from the charger: the handler's result, or an error frame.
    # Every request needs an answer, otherwise the charger stops sending.
    async def _handle_call(self, frame: Frame) -> None:
        unique_id = str(frame[1])
        if len(frame) < 4 or not isinstance(frame[3], dict):
            await self._send(build_call_error(unique_id, ERROR_FORMATION_VIOLATION, "payload must be an object"))
            return
        handler = self._handlers.get(str(frame[2]))
        if handler is None:
            await self._send(build_call_error(unique_id, ERROR_NOT_IMPLEMENTED, f"{frame[2]} is not supported"))
            return
        try:
            result = handler(frame[3])
        except FormationError as error:
            await self._send(build_call_error(unique_id, ERROR_FORMATION_VIOLATION, str(error)))
        except Exception:  # noqa: BLE001
            # A bug in one handler must not end the connection; the charger gets an error and carries on.
            _LOGGER.exception("Handling %s from %s failed", frame[2], self.cp_id)
            await self._send(build_call_error(unique_id, ERROR_INTERNAL, "internal error"))
        else:
            await self._send(build_call_result(unique_id, result))

    # Replace the snapshot and tell the listener.
    # One place publishes, so listeners never miss a change.
    def _update(self, **changes: Any) -> None:
        self._snapshot = replace(self._snapshot, **changes)
        self._listener.on_snapshot(self._snapshot)

    # Replace one connector in the snapshot.
    # Connector changes build a new mapping; the snapshot itself stays immutable.
    def _update_connector(self, connector: ConnectorState) -> None:
        connectors = dict(self._snapshot.connectors)
        connectors[connector.connector_id] = connector
        self._update(connectors=connectors)

    # Look up a connector, creating an unknown one on first sight.
    # A charger may start a transaction before it ever sent a status for that connector.
    def _connector(self, connector_id: int) -> ConnectorState:
        return self._snapshot.connectors.get(connector_id) or ConnectorState(connector_id)

    # ISO 8601 UTC time as the protocol wants it.
    # The charger sets its clock from the answers to BootNotification and Heartbeat.
    def _now_iso(self) -> str:
        return self._clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # Record the charger's identity, accept it, and start the follow-up setup in the background.
    # Setup waits and sends requests of its own, so it must not block the answer.
    def _boot_notification(self, payload: dict[str, Any]) -> dict[str, Any]:
        info = ChargerInfo(
            vendor=payload.get("chargePointVendor"),
            model=payload.get("chargePointModel"),
            serial=payload.get("chargePointSerialNumber"),
            firmware=payload.get("firmwareVersion"),
        )
        self._update(info=info)
        self._start_task(self._post_boot_setup())
        return {"status": "Accepted", "currentTime": self._now_iso(), "interval": self._settings.heartbeat_interval_s}

    # Answer a heartbeat with the hub's time.
    # This is how the charger keeps its clock right.
    def _heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"currentTime": self._now_iso()}

    # Store the status of a connector, keeping the transaction that may be running on it.
    # The status is what Home Assistant shows as plugged, charging or available.
    def _status_notification(self, payload: dict[str, Any]) -> dict[str, Any]:
        connector_id = _int_field(payload, "connectorId")
        status = payload.get("status")
        if not isinstance(status, str):
            raise FormationError("status must be a string")
        info = payload.get("info")
        self._update_connector(
            replace(
                self._connector(connector_id),
                status=status,
                error_code=str(payload.get("errorCode", "NoError")),
                info=info if isinstance(info, str) else None,
            )
        )
        return {}

    # Keep the newest meter reading.
    # A payload without usable values is acknowledged and ignored.
    def _meter_values(self, payload: dict[str, Any]) -> dict[str, Any]:
        sample = parse_meter_values(payload, self._clock())
        if sample is not None:
            self._update(meter=sample)
        return {}

    # Hand out a transaction id and remember which connector it runs on.
    # Every tag is accepted: this is a private charger on a trusted network, and refusing one only strands the owner.
    def _start_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        connector_id = _int_field(payload, "connectorId")
        meter_start = payload.get("meterStart")
        transaction_id = self._next_transaction_id()
        self._update_connector(
            replace(
                self._connector(connector_id),
                transaction_id=transaction_id,
                id_tag=str(payload.get("idTag", "")),
                meter_start_wh=float(meter_start) if isinstance(meter_start, (int, float)) else None,
            )
        )
        return {"transactionId": transaction_id, "idTagInfo": {"status": "Accepted"}}

    # Clear the transaction from the connector that ran it.
    # The id comes from the charger, so a stop for an unknown id is acknowledged without touching other sessions.
    def _stop_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        transaction_id = _int_field(payload, "transactionId")
        for connector in self._snapshot.connectors.values():
            if connector.transaction_id == transaction_id:
                self._update_connector(replace(connector, transaction_id=None, id_tag=None, meter_start_wh=None))
        return {"idTagInfo": {"status": "Accepted"}}

    # Accept an authorisation request.
    # See _start_transaction: every tag is accepted.
    def _authorize(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"idTagInfo": {"status": "Accepted"}}

    # Refuse vendor-specific data transfers politely.
    # The hub knows no vendor extensions, and an explicit answer keeps the charger from retrying.
    def _data_transfer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "UnknownVendorId"}

    # Acknowledge notifications that need no action.
    # Firmware and diagnostics progress is for the vendor backend, which sees it through the mirror.
    def _acknowledge(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {}

    # Run a coroutine in the background for as long as the connection lives.
    # Tasks are cancelled when the connection ends, so none can outlive its session.
    def _start_task(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # Whether the charger has said who it is, in a BootNotification.
    def _has_booted(self) -> bool:
        info = self._snapshot.info
        return info.vendor is not None or info.model is not None

    # Ask the charger for the messages it did not send on its own after connecting: its boot, then its status.
    # A charger that reconnects after the hub restarted usually skips both, so the hub would know neither the model nor
    # the state. TriggerMessage is the standard way for a central system that joined late, and it only asks the charger
    # to report; nothing on the charger changes. The charger ignores requests for a while after connecting, hence the
    # wait, and an unanswered request is repeated like the setup is.
    async def _ask_for_introduction(self) -> None:
        delays = (self._settings.boot_wait_s, *self._settings.setup_retry_delays_s)
        for attempt, delay in enumerate(delays, start=1):
            await asyncio.sleep(delay)
            try:
                if not self._has_booted():
                    await self.call("TriggerMessage", commands.trigger_message("BootNotification"))
                    await asyncio.sleep(self._settings.trigger_settle_s)
                if self._has_booted() and PRIMARY_CONNECTOR not in self._snapshot.connectors:
                    await self.call("TriggerMessage", commands.trigger_message("StatusNotification", PRIMARY_CONNECTOR))
            except CallError as error:
                _LOGGER.warning(
                    "Charger %s did not answer the request to introduce itself (try %s of %s): %s",
                    self.cp_id,
                    attempt,
                    len(delays),
                    error,
                )
                continue
            if self._has_booted() and PRIMARY_CONNECTOR in self._snapshot.connectors:
                _LOGGER.info("Charger %s introduced itself after being asked (try %s)", self.cp_id, attempt)
                return

    # After a charger booted: read its configuration and make sure it sends the meter values the hub needs.
    # A charger that does not answer is asked again a few times; failures are logged, not raised, because the charger
    # works without this and its next boot starts over.
    async def _post_boot_setup(self) -> None:
        delays = (self._settings.setup_delay_s, *self._settings.setup_retry_delays_s)
        for attempt, delay in enumerate(delays, start=1):
            await asyncio.sleep(delay)
            try:
                await self._read_configuration()
                await self._ensure_metering()
            except CallError as error:
                _LOGGER.warning(
                    "Setup of charger %s did not complete (try %s of %s): %s", self.cp_id, attempt, len(delays), error
                )
            else:
                _LOGGER.info("Setup of charger %s completed on try %s", self.cp_id, attempt)
                return
        _LOGGER.warning("Giving up on the setup of charger %s until its next boot", self.cp_id)

    # Read the configuration keys that decide what the hub can do and store them in the snapshot.
    # SupportedFeatureProfiles tells whether smart charging is offered at all. Only the keys asked for are kept: some
    # chargers answer with every key they have, and the snapshot is shown in diagnostics and to the vendor.
    async def _read_configuration(self) -> None:
        answer = await self.call("GetConfiguration", commands.get_configuration(CONFIG_KEYS))
        configuration = {
            str(item["key"]): str(item.get("value", ""))
            for item in answer.get("configurationKey", [])
            if isinstance(item, dict) and item.get("key") in CONFIG_KEYS
        }
        profiles = configuration.get("SupportedFeatureProfiles", "")
        features = frozenset(part.strip() for part in profiles.split(",") if part.strip())
        self._update(configuration=configuration, features=features)

    # Ask the charger to report the measurands the hub uses, at the configured interval, when it does not already.
    # A charger reports only what it is configured to, and the per-phase current is what load balancing needs.
    async def _ensure_metering(self) -> None:
        wanted = {
            "MeterValuesSampledData": self._settings.measurands,
            "MeterValueSampleInterval": str(self._settings.meter_sample_interval_s),
        }
        for key, value in wanted.items():
            if key in self._snapshot.configuration and self._snapshot.configuration[key] != value:
                answer = await self.call("ChangeConfiguration", commands.change_configuration(key, value))
                _LOGGER.info("Charger %s: %s set to %s, answer %s", self.cp_id, key, value, answer.get("status"))
                if answer.get("status") in ("Accepted", "RebootRequired"):
                    self._update(configuration={**self._snapshot.configuration, key: value})
