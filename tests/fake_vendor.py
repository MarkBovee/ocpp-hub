"""A fake vendor backend for mirror tests: a real websocket server that answers like an OCPP central system."""

from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import Callable
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.typing import Subprotocol


# Poll until a condition holds, so tests wait for an effect instead of sleeping a guessed time.
# A condition that never comes true fails the test with a message instead of hanging it.
async def eventually(check: Callable[[], object], timeout: float = 5.0, message: str = "condition not met") -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(message)
        await asyncio.sleep(0.01)


class FakeVendor:
    """Accepts the mirror's connections, records every request and answers each one the way a backend would."""

    # Choose how the backend answers a BootNotification; nothing listens until start() is called.
    def __init__(self, boot_status: str = "Accepted", interval: int = 300) -> None:
        self.boot_status = boot_status
        self.interval = interval
        # What each connection looked like when it opened, in order of arrival.
        self.paths: list[str] = []
        self.authorizations: list[str | None] = []
        self.subprotocols: list[str | None] = []
        # Every request received, across all connections, in order of arrival.
        self.calls: list[list[Any]] = []
        self.closed_count = 0
        self._tls = False
        self._port = 0
        self._server: Server | None = None
        self._connections: list[ServerConnection] = []
        self._transaction_counter = 7000
        self._gate: asyncio.Event | None = None
        self._results: dict[str, asyncio.Future[list[Any]]] = {}
        self._command_counter = 0

    # Start listening on a free port, or on the port used before so a restarted backend is found again.
    async def start(self, ssl_context: ssl.SSLContext | None = None) -> None:
        self._tls = ssl_context is not None
        self._server = await serve(
            self._handle, "127.0.0.1", self._port, subprotocols=[Subprotocol("ocpp1.6")], ssl=ssl_context
        )
        self._port = int(next(iter(self._server.sockets)).getsockname()[1])

    # Stop listening and drop every connection, as a backend that goes down would.
    async def stop(self) -> None:
        self.release()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @property
    def connection_count(self) -> int:
        """How many connections were opened so far."""
        return len(self.paths)

    @property
    def url(self) -> str:
        """The base URL the mirror is configured with; it has a path so the identity must be appended to it."""
        return f"{'wss' if self._tls else 'ws'}://127.0.0.1:{self._port}/ocpp"

    # Stop answering: requests are recorded but their answers wait until release() is called.
    def hold(self) -> None:
        self._gate = asyncio.Event()

    # Answer everything that was held back, and answer normally again.
    def release(self) -> None:
        if self._gate is not None:
            self._gate.set()
            self._gate = None

    # The actions of the requests received so far, in order.
    def actions(self) -> list[str]:
        return [call[2] for call in self.calls]

    # The requests received for one action, in order.
    def calls_of(self, action: str) -> list[list[Any]]:
        return [call for call in self.calls if call[2] == action]

    # Send a command to the mirror on the newest connection and return its answer frame.
    async def command(self, action: str, payload: dict[str, Any], timeout: float = 3.0) -> list[Any]:
        self._command_counter += 1
        unique_id = f"v{self._command_counter}"
        future: asyncio.Future[list[Any]] = asyncio.get_running_loop().create_future()
        self._results[unique_id] = future
        await self._connections[-1].send(json.dumps([2, unique_id, action, payload]))
        return await asyncio.wait_for(future, timeout)

    # Serve one connection: record it, then answer its requests until it closes.
    async def _handle(self, connection: ServerConnection) -> None:
        self.paths.append(connection.request.path)
        self.authorizations.append(connection.request.headers.get("Authorization"))
        self.subprotocols.append(connection.subprotocol)
        self._connections.append(connection)
        try:
            async for message in connection:
                frame = json.loads(message)
                if frame[0] == 2:
                    self.calls.append(frame)
                    gate = self._gate
                    if gate is not None:
                        await gate.wait()
                    await connection.send(json.dumps([3, frame[1], self._answer(frame[2])]))
                elif frame[1] in self._results and not self._results[frame[1]].done():
                    self._results[frame[1]].set_result(frame)
        except ConnectionClosed:
            pass
        finally:
            self.closed_count += 1

    # The payload a central system answers to one action.
    def _answer(self, action: str) -> dict[str, Any]:
        now = "2026-10-20T18:00:00Z"
        if action == "BootNotification":
            return {"status": self.boot_status, "currentTime": now, "interval": self.interval}
        if action == "StartTransaction":
            self._transaction_counter += 1
            return {"transactionId": self._transaction_counter, "idTagInfo": {"status": "Accepted"}}
        if action == "Heartbeat":
            return {"currentTime": now}
        if action in ("StopTransaction", "Authorize"):
            return {"idTagInfo": {"status": "Accepted"}}
        return {}
