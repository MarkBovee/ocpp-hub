"""Transaction ids on the way to the vendor backend: pairing the two StartTransaction answers and rewriting frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .mirror_queue import ACTION_METER, ACTION_STOP, is_integer
from .txid_map import TransactionIdMap


@dataclass(slots=True)
class _Halves:
    """The two answers to one StartTransaction; either may arrive first."""

    hub_id: int | None = None
    vendor_id: int | None = None


class StartPairing:
    """Learns which vendor transaction belongs to which hub transaction.

    The hub answers a StartTransaction with its own id and the vendor answers the same request with another one.
    Both answers carry the unique id of the request, so they are matched on that and registered once both are known.
    """

    # Remember the map to fill and how many unanswered starts to keep track of.
    # The limit keeps a vendor that never answers from growing the memory of a long-running Home Assistant.
    def __init__(self, transactions: TransactionIdMap, limit: int = 64) -> None:
        self._transactions = transactions
        self._limit = limit
        self._open: dict[str, _Halves] = {}

    # Start watching the answers to one StartTransaction request.
    def expect(self, unique_id: str) -> None:
        self._open[unique_id] = _Halves()
        while len(self._open) > self._limit:
            del self._open[next(iter(self._open))]

    # Record the hub's transaction id for a request being watched.
    def hub_answered(self, unique_id: str, hub_id: int) -> None:
        halves = self._open.get(unique_id)
        if halves is not None:
            halves.hub_id = hub_id
            self._complete(unique_id, halves)

    # Record the vendor's transaction id for a request being watched.
    def vendor_answered(self, unique_id: str, vendor_id: int) -> None:
        halves = self._open.get(unique_id)
        if halves is not None:
            halves.vendor_id = vendor_id
            self._complete(unique_id, halves)

    # Stop watching a request that failed on either side, so it cannot pair with anything later.
    def abandon(self, unique_id: str) -> None:
        self._open.pop(unique_id, None)

    # Register the pair as soon as both ids are known.
    def _complete(self, unique_id: str, halves: _Halves) -> None:
        if halves.hub_id is not None and halves.vendor_id is not None:
            self._transactions.register(halves.hub_id, halves.vendor_id)
            del self._open[unique_id]


@dataclass(frozen=True, slots=True)
class Translation:
    """A payload ready for the vendor, or the reason it cannot be sent."""

    payload: dict[str, Any]
    send: bool = True
    note: str | None = None


# Give a frame the vendor's transaction id instead of the hub's.
# A meter reading without a known transaction is still worth sending, just without the id. A stop without one is not:
# the vendor cannot match it to anything, and an invented id could end another session.
def translate_for_vendor(action: str, payload: dict[str, Any], transactions: TransactionIdMap) -> Translation:
    if action not in (ACTION_METER, ACTION_STOP) or "transactionId" not in payload:
        return Translation(payload)
    hub_id = payload["transactionId"]
    vendor_id = transactions.to_vendor(hub_id) if is_integer(hub_id) else None
    if vendor_id is not None:
        return Translation({**payload, "transactionId": vendor_id})
    note = f"transaction {hub_id} is not known to the vendor backend"
    if action == ACTION_STOP:
        return Translation(payload, send=False, note=note)
    return Translation({key: value for key, value in payload.items() if key != "transactionId"}, note=note)
