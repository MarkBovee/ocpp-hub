"""Request payload builders and the transaction id map."""

from __future__ import annotations

from ocpp_hub_core import commands
from ocpp_hub_core.txid_map import TransactionIdMap


# Intent: a flat ampere limit is a single period at second zero in a profile of the chosen purpose.
# Why: this is the exact shape the charger must accept; a deviation is a different (and possibly rejected) request.
def test_charging_profile_shape() -> None:
    payload = commands.set_charging_profile(0, commands.PURPOSE_CHARGE_POINT_MAX, 10, profile_id=1000)

    profile = payload["csChargingProfiles"]
    assert payload["connectorId"] == 0
    assert profile["chargingProfilePurpose"] == "ChargePointMaxProfile" and profile["stackLevel"] == 0
    assert profile["chargingSchedule"] == {
        "chargingRateUnit": "A",
        "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 10}],
    }
    assert "transactionId" not in profile


# Intent: only a TxProfile carries the transaction id.
# Why: the protocol forbids a transaction id on the other purposes and some chargers reject it.
def test_transaction_id_only_on_tx_profile() -> None:
    tx = commands.set_charging_profile(1, commands.PURPOSE_TX, 8, 3001, transaction_id=42)
    default = commands.set_charging_profile(1, commands.PURPOSE_TX_DEFAULT, 8, 2001, transaction_id=42)

    assert tx["csChargingProfiles"]["transactionId"] == 42
    assert "transactionId" not in default["csChargingProfiles"]


# Intent: the small builders produce exactly the keys the protocol names.
# Why: a misspelled key is silently ignored by a lenient charger and fails only on a strict one.
def test_small_builders() -> None:
    assert commands.get_configuration() == {} and commands.get_configuration(["A", "B"]) == {"key": ["A", "B"]}
    assert commands.change_configuration("K", "v") == {"key": "K", "value": "v"}
    assert commands.trigger_message("StatusNotification", 1) == {
        "requestedMessage": "StatusNotification",
        "connectorId": 1,
    }
    assert commands.trigger_message("BootNotification") == {"requestedMessage": "BootNotification"}
    assert commands.clear_charging_profile(7) == {"id": 7}
    assert commands.remote_start_transaction("tag", 1) == {"idTag": "tag", "connectorId": 1}
    assert commands.remote_stop_transaction(5) == {"transactionId": 5}


# Intent: ids translate both ways, unknown ids give None, and forgetting releases both directions.
# Why: a wrong or stale mapping would apply a vendor command to someone else's session.
def test_transaction_id_map_round_trip_and_forget() -> None:
    ids = TransactionIdMap()
    ids.register(hub_id=100, vendor_id=9001)

    assert ids.to_vendor(100) == 9001 and ids.to_hub(9001) == 100 and len(ids) == 1
    assert ids.to_vendor(101) is None and ids.to_hub(1) is None
    ids.forget(100)
    assert ids.to_vendor(100) is None and ids.to_hub(9001) is None and len(ids) == 0
    ids.forget(100)
