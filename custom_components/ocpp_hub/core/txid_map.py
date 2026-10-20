"""Two-way mapping between the transaction ids of the hub and those of the vendor backend."""

from __future__ import annotations


class TransactionIdMap:
    """The hub is the central system for the charger and hands out ids; the vendor backend hands out its own.

    The mirror shows the vendor a charger whose transactions carry the vendor's ids, so every frame that names a
    transaction is translated on its way to the vendor, and every command from the vendor on its way back.
    """

    # Start with no transactions known.
    # The map fills as StartTransaction answers arrive from both sides.
    def __init__(self) -> None:
        self._hub_to_vendor: dict[int, int] = {}
        self._vendor_to_hub: dict[int, int] = {}

    # Remember that a hub transaction is the vendor's transaction.
    # Later frames of the same session must carry the id the vendor knows.
    def register(self, hub_id: int, vendor_id: int) -> None:
        self._hub_to_vendor[hub_id] = vendor_id
        self._vendor_to_hub[vendor_id] = hub_id

    # Translate a hub id for the vendor, or None when the vendor never confirmed that transaction.
    # None lets the caller decide to drop or flag the frame instead of sending an id the vendor cannot match.
    def to_vendor(self, hub_id: int) -> int | None:
        return self._hub_to_vendor.get(hub_id)

    # Translate a vendor id for the hub, or None when it is not ours.
    # A command naming an unknown transaction must be refused rather than applied to another session.
    def to_hub(self, vendor_id: int) -> int | None:
        return self._vendor_to_hub.get(vendor_id)

    # Forget a finished transaction.
    # The map would otherwise grow for as long as Home Assistant runs.
    def forget(self, hub_id: int) -> None:
        vendor_id = self._hub_to_vendor.pop(hub_id, None)
        if vendor_id is not None:
            self._vendor_to_hub.pop(vendor_id, None)

    # Report how many transactions are mapped.
    # Diagnostics and tests need to see that finished sessions are released.
    def __len__(self) -> int:
        return len(self._hub_to_vendor)
