"""The pure parts of the vendor mirror: queue policy, transaction id handling, command answers and settings."""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest
from ocpp_hub_core.mirror_commands import TriggerRequest, answer_vendor_call
from ocpp_hub_core.mirror_model import MirrorSettings, MirrorSettingsError
from ocpp_hub_core.mirror_queue import Outgoing, OutgoingQueue
from ocpp_hub_core.mirror_rewrite import StartPairing, translate_for_vendor
from ocpp_hub_core.txid_map import TransactionIdMap


# A queue item with a unique id derived from its content, so tests can tell frames apart.
def _item(action: str, connector: int | None = None, **payload: Any) -> Outgoing:
    if connector is not None:
        payload["connectorId"] = connector
    return Outgoing(f"{action}-{connector}-{len(payload)}", action, payload)


# A generator of fresh ids for replayed frames.
def _ids() -> Any:
    counter = itertools.count(1)
    return lambda: f"new-{next(counter)}"


# A TriggerMessage check that always says no.
def _no(request: TriggerRequest) -> bool:
    return False


# Intent: a full queue sheds the oldest meter value or status change first and keeps the newest.
# Why: a reading is replaced by the next one, but a lost StartTransaction would leave the vendor without a session.
def test_full_queue_drops_the_oldest_droppable_frame() -> None:
    queue = OutgoingQueue(3)

    dropped = [queue.push(_item(action, 1)) for action in ("BootNotification", "MeterValues", "StatusNotification")]
    dropped.append(queue.push(_item("StartTransaction", 1)))
    dropped.append(queue.push(_item("MeterValues", 1, extra=1)))

    assert dropped == [0, 0, 0, 1, 1]
    assert queue.actions == ["BootNotification", "StartTransaction", "MeterValues"]


# Intent: frames that must not be lost stay queued even when they exceed the capacity.
# Why: the size limit exists to bound memory for readings, not to throw away boots, starts and stops.
def test_essential_frames_are_never_dropped() -> None:
    queue = OutgoingQueue(2)

    dropped = sum(queue.push(_item(action)) for action in ("StartTransaction", "StopTransaction", "Authorize"))

    assert dropped == 0 and len(queue) == 3 and queue.over_capacity
    assert queue.pop() == _item("StartTransaction")


# Intent: a new vendor connection starts with one boot and one status per connector, each under a new id.
# Why: the vendor sees a new charge point and must learn its state before any later frame makes sense.
def test_begin_session_replays_boot_and_connector_states() -> None:
    queue = OutgoingQueue(10)
    boot, status0, status1 = _item("BootNotification"), _item("StatusNotification", 0), _item("StatusNotification", 1)
    queue.push(boot)

    queue.begin_session(boot, {0: status0, 1: status1}, _ids())

    assert [(item.action, item.unique_id) for item in iter(queue.pop, None)] == [
        ("BootNotification", "new-1"),
        ("StatusNotification", "new-2"),
        ("StatusNotification", "new-3"),
    ]


# Intent: a status change that is still waiting is not replayed from the cache, and queued frames keep their order.
# Why: replaying the latest state ahead of older queued changes would make the vendor see the connector flip back.
def test_begin_session_keeps_waiting_frames_in_order_and_skips_covered_connectors() -> None:
    queue = OutgoingQueue(10)
    boot, status0 = _item("BootNotification"), _item("StatusNotification", 0)
    preparing, start = _item("StatusNotification", 1, status="Preparing"), _item("StartTransaction", 1)
    queue.push(preparing)
    queue.push(start)

    queue.begin_session(boot, {0: status0, 1: preparing}, _ids())

    assert [(item.action, item.payload.get("connectorId")) for item in iter(queue.pop, None)] == [
        ("BootNotification", None),
        ("StatusNotification", 0),
        ("StatusNotification", 1),
        ("StartTransaction", 1),
    ]


# Intent: a frame that was sent and not answered goes back to the front of the queue.
# Why: a connection that dies mid-request must not reorder or lose what the vendor still has to hear.
def test_unanswered_frame_returns_to_the_front() -> None:
    queue = OutgoingQueue(10)
    queue.push(_item("StopTransaction"))

    queue.push_front(_item("StartTransaction"))

    assert queue.actions == ["StartTransaction", "StopTransaction"]


# Intent: the two answers to a StartTransaction pair up in either order and are registered once both are known.
# Why: the vendor can answer before or after the hub's own answer has been seen.
@pytest.mark.parametrize("hub_first", [True, False])
def test_start_pairing_registers_in_either_order(hub_first: bool) -> None:
    transactions = TransactionIdMap()
    pairing = StartPairing(transactions)
    pairing.expect("u1")

    answers = [lambda: pairing.hub_answered("u1", 11), lambda: pairing.vendor_answered("u1", 99)]
    for answer in answers if hub_first else reversed(answers):
        assert transactions.to_vendor(11) is None
        answer()

    assert transactions.to_vendor(11) == 99 and transactions.to_hub(99) == 11


# Intent: an abandoned or unknown start never registers anything, and the pairing forgets old starts.
# Why: a failed start must not claim an id later, and a silent vendor must not grow memory without bound.
def test_start_pairing_ignores_abandoned_and_unknown_requests() -> None:
    transactions = TransactionIdMap()
    pairing = StartPairing(transactions, limit=2)
    pairing.expect("a")
    pairing.abandon("a")
    pairing.hub_answered("a", 1)
    pairing.vendor_answered("a", 2)
    pairing.hub_answered("never-expected", 3)
    for name in ("b", "c", "d"):
        pairing.expect(name)
    pairing.hub_answered("b", 4)
    pairing.vendor_answered("b", 5)

    assert len(transactions) == 0


# Intent: meter values and stops get the vendor's transaction id; everything else passes unchanged.
# Why: the vendor only knows its own ids, and a frame with a foreign id would be matched to the wrong session.
def test_translation_uses_the_vendors_id() -> None:
    transactions = TransactionIdMap()
    transactions.register(11, 99)

    meter = translate_for_vendor("MeterValues", {"connectorId": 1, "transactionId": 11}, transactions)
    stop = translate_for_vendor("StopTransaction", {"transactionId": 11, "meterStop": 5}, transactions)
    other = translate_for_vendor("StatusNotification", {"connectorId": 1, "transactionId": 11}, transactions)

    assert meter.payload == {"connectorId": 1, "transactionId": 99} and meter.send and meter.note is None
    assert stop.payload == {"transactionId": 99, "meterStop": 5} and stop.send
    assert other.payload == {"connectorId": 1, "transactionId": 11}


# Intent: an unknown transaction id is dropped from a meter reading and blocks a stop.
# Why: a reading is still useful without the id, but a stop with a made-up id could end another session.
@pytest.mark.parametrize("hub_id", [12, "12", True])
def test_translation_of_an_unknown_transaction(hub_id: object) -> None:
    transactions = TransactionIdMap()

    meter = translate_for_vendor("MeterValues", {"connectorId": 1, "transactionId": hub_id}, transactions)
    stop = translate_for_vendor("StopTransaction", {"transactionId": hub_id}, transactions)

    assert meter.payload == {"connectorId": 1} and meter.send and meter.note
    assert stop.send is False and stop.note and stop.payload == {"transactionId": hub_id}
    assert translate_for_vendor("MeterValues", {"connectorId": 1}, transactions).note is None


# Intent: GetConfiguration is answered from what the hub read, read-only, with unknown keys listed.
# Why: the vendor sees the real charger settings but must not believe it can change them.
def test_get_configuration_answer() -> None:
    configuration = {"HeartbeatInterval": "3600", "NumberOfConnectors": "1"}

    asked = {"key": ["HeartbeatInterval", "Nope"]}
    some = json.loads(answer_vendor_call("c1", "GetConfiguration", asked, configuration, _no).frame)
    every = json.loads(answer_vendor_call("c2", "GetConfiguration", {}, configuration, _no).frame)

    known = {"key": "HeartbeatInterval", "readonly": True, "value": "3600"}
    assert some == [3, "c1", {"configurationKey": [known], "unknownKey": ["Nope"]}]
    assert [item["key"] for item in every[2]["configurationKey"]] == ["HeartbeatInterval", "NumberOfConnectors"]
    assert "unknownKey" not in every[2]


# Intent: TriggerMessage is accepted only for Boot and Status frames the mirror has, and bad input is a formation error.
# Why: answering Accepted without sending anything would make the vendor wait for a frame that never comes.
@pytest.mark.parametrize(
    ("payload", "can", "expected"),
    [
        ({"requestedMessage": "BootNotification"}, True, {"status": "Accepted"}),
        ({"requestedMessage": "BootNotification"}, False, {"status": "Rejected"}),
        ({"requestedMessage": "MeterValues"}, True, {"status": "NotImplemented"}),
        ({"requestedMessage": 5}, True, "FormationViolation"),
        ({"requestedMessage": "StatusNotification", "connectorId": "1"}, True, "FormationViolation"),
    ],
)
def test_trigger_message_answers(payload: dict[str, Any], can: bool, expected: object) -> None:
    answer = answer_vendor_call("t1", "TriggerMessage", payload, {}, lambda request: can)

    frame = json.loads(answer.frame)
    assert frame[2] == expected
    assert (answer.trigger is not None) == (expected == {"status": "Accepted"})


# Intent: bad settings are refused when they are created, each with a code a form can show a message for.
# Why: a typo in the vendor URL or half a credential pair must show up as an error, not as a retry loop forever.
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"url": "http://vendor.example/ocpp"}, "invalid_url"),
        ({"url": "ws://"}, "invalid_url"),
        ({"url": "ws://[::1/ocpp"}, "invalid_url"),
        ({"url": "ws://vendor.example:abc/ocpp"}, "invalid_url"),
        ({"url": "wss://user:secret@vendor.example/ocpp"}, "credentials_in_url"),
        ({"user": "only-a-user"}, "credentials_incomplete"),
        ({"password": "only-a-password"}, "credentials_incomplete"),
        ({"commands": "everything"}, "invalid_settings"),
        ({"max_queue": 0}, "invalid_settings"),
        ({"reply_timeout_s": 0.0}, "invalid_settings"),
        ({"backoff_min_s": 5.0, "backoff_max_s": 1.0}, "invalid_settings"),
    ],
)
def test_invalid_settings_are_refused(overrides: dict[str, Any], code: str) -> None:
    with pytest.raises(MirrorSettingsError) as refused:
        MirrorSettings(**{"url": "wss://vendor.example/ocpp", **overrides})

    assert refused.value.code == code and isinstance(refused.value, ValueError)


# Intent: credentials give an auth header and never show up in the printed settings.
# Why: settings end up in logs and diagnostics, and a password there is a leak.
def test_credentials_are_used_for_the_header_and_kept_out_of_repr() -> None:
    settings = MirrorSettings(url="wss://vendor.example/ocpp", user="mirror", password="s3cret")

    assert settings.auth_header is not None and settings.auth_header[0] == "Authorization"
    assert "s3cret" not in repr(settings) and settings.use_tls
    assert MirrorSettings(url="ws://vendor.example/ocpp").auth_header is None


# Intent: the Raedian address (secure websocket, explicit port, path with a trailing slash) is accepted without
# credentials, uses TLS, sends no auth header, and the charger id is appended once to the path.
# Why: this is the endpoint the NEO was measured to use; a refactor of the URL checks must not turn it away.
def test_the_raedian_address_is_accepted_and_gets_the_charger_id_appended() -> None:
    from ocpp_hub_core.frames import join_websocket_url

    settings = MirrorSettings(url="wss://cloud-protocol.raedian.com:9043/ocpp/")

    assert settings.use_tls and settings.auth_header is None
    assert join_websocket_url(settings.url, "CHARGER1") == "wss://cloud-protocol.raedian.com:9043/ocpp/CHARGER1"


# Intent: the same address without the trailing slash gives the same connection URL.
# Why: owners type it both ways, and a missing slash must not glue the charger id onto "ocpp".
def test_the_vendor_address_works_with_and_without_a_trailing_slash() -> None:
    from ocpp_hub_core.frames import join_websocket_url

    with_slash = join_websocket_url("wss://vendor.example:9043/ocpp/", "CP1")
    without_slash = join_websocket_url("wss://vendor.example:9043/ocpp", "CP1")

    assert with_slash == without_slash == "wss://vendor.example:9043/ocpp/CP1"
