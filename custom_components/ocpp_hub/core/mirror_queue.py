"""The bounded queue of frames waiting to go to the vendor backend, with the rules for what may be dropped."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, TypeGuard

ACTION_BOOT = "BootNotification"
ACTION_STATUS = "StatusNotification"
ACTION_METER = "MeterValues"
ACTION_HEARTBEAT = "Heartbeat"
ACTION_START = "StartTransaction"
ACTION_STOP = "StopTransaction"

# Only these may be thrown away when the queue is full: the next reading or status change replaces them.
# Boot, start, stop, authorize and data transfer carry facts that cannot be reconstructed, so they are never dropped.
DROPPABLE_ACTIONS = frozenset({ACTION_METER, ACTION_STATUS})


@dataclass(frozen=True, slots=True)
class Outgoing:
    """A request the mirror still owes the vendor backend. The payload carries the hub's ids, not the vendor's."""

    unique_id: str
    action: str
    payload: dict[str, Any]


# True for a real integer. A bool is an int in Python but never a connector or transaction id.
def is_integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


# The connector a status or meter frame is about, or None when the payload does not name one properly.
def connector_of(item: Outgoing) -> int | None:
    connector = item.payload.get("connectorId")
    return connector if is_integer(connector) else None


class OutgoingQueue:
    """First in, first out, with a size limit that sacrifices meter values and status changes before anything else."""

    # Start empty with a fixed capacity.
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._items: deque[Outgoing] = deque()

    # Report how many frames wait.
    def __len__(self) -> int:
        return len(self._items)

    @property
    def over_capacity(self) -> bool:
        """True when only frames that may never be dropped are left and they still exceed the capacity."""
        return len(self._items) > self._capacity

    @property
    def actions(self) -> list[str]:
        """The actions of the waiting frames, oldest first (for logs and tests)."""
        return [item.action for item in self._items]

    # Add a frame at the back and return how many frames had to be dropped to stay within the capacity.
    def push(self, item: Outgoing) -> int:
        self._items.append(item)
        return self._trim()

    # Put a frame back at the front, for a frame that was sent but never answered.
    # It keeps its place ahead of everything that arrived later.
    def push_front(self, item: Outgoing) -> None:
        self._items.appendleft(item)

    # Take the oldest frame, or None when the queue is empty.
    def pop(self) -> Outgoing | None:
        return self._items.popleft() if self._items else None

    # Prepare the queue for a fresh vendor connection.
    # The vendor sees a new charge point, so it needs a BootNotification first and the current state of every
    # connector. A status change still waiting in the queue already says that, so only connectors without one get
    # their cached status again. Replayed frames get new unique ids because the vendor has seen the old ones.
    def begin_session(self, boot: Outgoing | None, statuses: Mapping[int, Outgoing], new_id: Callable[[], str]) -> None:
        kept = [item for item in self._items if boot is None or item.action != ACTION_BOOT]
        waiting = {connector_of(item) for item in kept if item.action == ACTION_STATUS}
        front = [replace(boot, unique_id=new_id())] if boot is not None else []
        front += [
            replace(item, unique_id=new_id())
            for connector, item in sorted(statuses.items())
            if connector not in waiting
        ]
        self._items = deque([*front, *kept])

    # Drop the oldest meter value or status change until the queue fits again, or none is left to drop.
    # Returns the number of dropped frames. Frames that must not be lost may leave the queue over capacity.
    def _trim(self) -> int:
        dropped = 0
        while len(self._items) > self._capacity:
            index = next((i for i, item in enumerate(self._items) if item.action in DROPPABLE_ACTIONS), None)
            if index is None:
                break
            del self._items[index]
            dropped += 1
        return dropped
