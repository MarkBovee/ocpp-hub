"""The central system: the websocket server chargers connect to, one ChargerSession per charger."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve

from .frames import Frame
from .model import ChargerSnapshot, ChargeSession, ConnectorState
from .session import ChargerSession, SessionListener, SessionSettings

_LOGGER = logging.getLogger(__name__)

# Subprotocols a charger may negotiate. Only 1.6 is implemented; the others are listed so a charger that offers
# several still connects and the session can refuse what it does not speak.
SUPPORTED_SUBPROTOCOLS = ["ocpp1.6"]

CLOSE_BAD_REQUEST = 4001


class CentralSystem:
    """Runs the server, creates a session per charger and hands out transaction ids."""

    # Keep the listeners and settings; nothing listens until start() is called.
    def __init__(
        self,
        listeners: Sequence[SessionListener],
        settings: SessionSettings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._listeners = list(listeners)
        self._settings = settings
        self._clock = clock
        self._server: Server | None = None
        self._sessions: dict[str, ChargerSession] = {}
        # Seeded from the clock so ids from before a restart cannot collide with ids a charger still holds offline.
        self._transaction_counter = int(time.time()) % 2**31
        # The charges that were running when a charger's connection ended, until the charger comes back.
        self._open_charges: dict[str, dict[int, ConnectorState]] = {}

    @property
    def port(self) -> int:
        """The port the server really listens on (useful when started on port 0 in tests)."""
        assert self._server is not None, "start() was not called"
        return int(next(iter(self._server.sockets)).getsockname()[1])

    # Start listening on the given interface and port.
    async def start(self, host: str, port: int) -> None:
        self._server = await serve(self._handle, host, port, subprotocols=list(SUPPORTED_SUBPROTOCOLS))
        _LOGGER.info("OCPP server listening on %s:%s", host, port)

    # Stop accepting chargers and drop the open connections without a closing handshake.
    # A charger that is closed properly waits minutes before it dials again, one that loses its connection comes back
    # within half a minute, and a reload of the integration should not cost the owner his charger for minutes.
    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close(close_connections=False)
        for session in list(self._sessions.values()):
            session.abort()
        await self._server.wait_closed()
        self._server = None

    # Return the live session of a charger, or None when it is not connected.
    # Commands to a charger go through its session.
    def session(self, cp_id: str) -> ChargerSession | None:
        return self._sessions.get(cp_id)

    # The snapshots of all connected chargers.
    # Used for diagnostics and by tests.
    def snapshots(self) -> list[ChargerSnapshot]:
        return [session.snapshot for session in self._sessions.values()]

    # Hand out the next transaction id.
    # One counter for all chargers keeps ids unique across reconnects within a run.
    def _next_transaction_id(self) -> int:
        self._transaction_counter += 1
        return self._transaction_counter

    # Handle one incoming charger connection until it ends.
    # The identity is the last path segment of the URL; a connection without identity or protocol is refused.
    async def _handle(self, connection: ServerConnection) -> None:
        cp_id = urlsplit(connection.request.path).path.strip("/").rsplit("/", 1)[-1]
        protocol = connection.subprotocol
        if not cp_id or not protocol:
            _LOGGER.warning("Refusing a connection without charger identity or OCPP subprotocol")
            await connection.close(CLOSE_BAD_REQUEST, "missing charge point id or OCPP subprotocol")
            return
        previous = self._sessions.get(cp_id)
        if previous is not None:
            # The charger came back before the hub noticed the old link dying.
            _LOGGER.info("Charger %s reconnected; closing its previous connection", cp_id)
            await previous.close()
        # A charge that was running when the charger's last connection ended goes on with the new one.
        resume = _running_connectors(previous.snapshot) if previous is not None else self._open_charges.pop(cp_id, {})
        session = ChargerSession(
            cp_id,
            protocol,
            connection,
            _Fanout(self._listeners),
            self._next_transaction_id,
            self._settings,
            self._clock,
            resume,
        )
        self._sessions[cp_id] = session
        _LOGGER.info("Charger %s connected (%s)", cp_id, protocol)
        try:
            await session.run()
        finally:
            if self._sessions.get(cp_id) is session:
                del self._sessions[cp_id]
                running = _running_connectors(session.snapshot)
                if running:
                    self._open_charges[cp_id] = running
            _LOGGER.info("Charger %s disconnected", cp_id)


# The connectors of a snapshot that have a charge running.
def _running_connectors(snapshot: ChargerSnapshot) -> dict[int, ConnectorState]:
    return {number: c for number, c in snapshot.connectors.items() if c.transaction_id is not None}


class _Fanout:
    """Passes every listener event to all listeners; one failing listener never blocks the others."""

    # Keep the listeners to call.
    # The session knows a single listener, so the central system hands it this fan-out.
    def __init__(self, listeners: Sequence[SessionListener]) -> None:
        self._listeners = listeners

    # Call one method on every listener, logging instead of raising.
    # A bug in the Home Assistant layer or the mirror must not take the charger connection down.
    def _each(self, method: str, *args: Any) -> None:
        for listener in self._listeners:
            try:
                getattr(listener, method)(*args)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Listener %s failed in %s", type(listener).__name__, method)

    # Forward a connection event.
    def on_connected(self, session: ChargerSession) -> None:
        self._each("on_connected", session)

    # Forward a state change.
    def on_snapshot(self, snapshot: ChargerSnapshot) -> None:
        self._each("on_snapshot", snapshot)

    # Forward a frame.
    def on_frame(self, cp_id: str, sender: str, frame: Frame) -> None:
        self._each("on_frame", cp_id, sender, frame)

    # Forward a disconnect.
    def on_disconnected(self, cp_id: str) -> None:
        self._each("on_disconnected", cp_id)

    # Forward a finished charge.
    def on_session_finished(self, session: ChargeSession) -> None:
        self._each("on_session_finished", session)
