"""The Raedian planning protocol, decoded from frames that were captured from the app and the charger."""

from __future__ import annotations

import pytest
from ocpp_hub_core.raedian import (
    FrameError,
    PlanWindow,
    build_frame,
    decode_plan,
    encode_plan,
    parse_frame,
    plan_from_exchange,
)

SESSION = "e33dde46e9b0fcb2"
READ_REQUEST = "fea7000001000801" + SESSION + "00"
EMPTY_ANSWER = "aaa7000002000856" + SESSION + "0000"
WRITE_ACK = "aaa7000001000855" + SESSION + "00"
# Monday 12:00 to 14:00 and Tuesday 13:00 to 17:00, as the app wrote them.
WRITE_REQUEST = "fea700001a0008cf" + SESSION + "0102010001c0a80000e0c4000020010002d0b6000010ef000020"
MONDAY = PlanWindow(1, 43200, 50400)
TUESDAY = PlanWindow(2, 46800, 61200)


# Intent: frames captured from the real app and charger parse, their check byte (the XOR of the other bytes) is right,
# and building the same frame again gives the same bytes.
# Why: the check byte was inferred from twelve captured frames; this keeps the inference honest and the builder exact.
@pytest.mark.parametrize("text", [READ_REQUEST, EMPTY_ANSWER, WRITE_ACK, WRITE_REQUEST])
def test_captured_frames_parse_and_rebuild_exactly(text: str) -> None:
    frame = parse_frame(text)

    assert build_frame(frame.head, frame.command, frame.session, frame.payload) == text


# Intent: the two windows the owner set in the app decode to the weekdays and times that were entered, and encode back
# to the very bytes the app wrote.
# Why: this is the proof that the layout was read correctly: the numbers match what was typed in the app.
def test_the_captured_plan_decodes_to_what_was_entered_and_encodes_back() -> None:
    payload = parse_frame(WRITE_REQUEST).payload

    assert decode_plan(payload) == [MONDAY, TUESDAY]
    assert encode_plan([MONDAY, TUESDAY]) == payload


# Intent: a write that the charger acknowledged gives the plan, a read gives the charger's answer (empty is a plan of no
# windows), and anything else says nothing about the plan.
# Why: the plan is shown in Home Assistant, so it must come only from exchanges that were understood completely.
def test_the_plan_comes_only_from_understood_exchanges() -> None:
    assert plan_from_exchange(WRITE_REQUEST, WRITE_ACK) == [MONDAY, TUESDAY]
    assert plan_from_exchange(READ_REQUEST, EMPTY_ANSWER) == []
    assert plan_from_exchange(WRITE_REQUEST, EMPTY_ANSWER) is None
    assert plan_from_exchange(READ_REQUEST, "aaa7000001000800" + SESSION + "00") is None
    assert plan_from_exchange("feb1000001000817" + SESSION + "00", WRITE_ACK) is None


# Intent: damaged frames, a wrong check byte, a window with an impossible weekday or time, and an overnight flag that
# disagrees with the times are refused.
# Why: this data comes from a protocol that is guessed at; a payload is believed only when every byte is accounted for.
@pytest.mark.parametrize(
    "text",
    [
        "zz",
        "fea7",
        READ_REQUEST[:-2] + "01",
        READ_REQUEST.replace("fea7000001000801", "fea7000001000802"),
        "fea700001a0008cf" + SESSION + "0102010001c0a80000e0c4000020010002d0b6000010ef0000",
    ],
)
def test_damaged_frames_are_refused(text: str) -> None:
    with pytest.raises(FrameError):
        parse_frame(text)


# Intent: a window with a start the wrong way round, a weekday mask outside 1 to 127, an end past the day or a count
# that does not match the bytes is refused.
# Why: each of these means the layout was misread; refusing them is how a wrong guess shows up, not a wrong plan.
@pytest.mark.parametrize(
    "payload",
    [
        bytes.fromhex("0101") + bytes.fromhex("01000800c0a80000e0c4000020"),
        bytes.fromhex("0101010080c0a80000e0c4000020"),
        bytes.fromhex("0101010001e0c40000c0a8000020"),
        bytes.fromhex("01010100010100ffff0000000020"),
    ],
)
def test_impossible_windows_are_refused(payload: bytes) -> None:
    with pytest.raises(FrameError):
        decode_plan(payload)


# Intent: with forwarding on, a planning write of the app that the charger acknowledges shows as the app plan in the
# snapshot, and a later read that answers an empty plan clears it.
# Why: this is how the plan reaches Home Assistant without the hub asking the charger anything of its own.
async def test_the_app_plan_follows_the_exchanges_that_pass_through(start_rig) -> None:
    import asyncio
    import json

    from tests.fake_vendor import eventually

    rig = await start_rig(commands="forward")
    charger = await rig.hub.charger()
    await charger.boot()
    await eventually(lambda: rig.vendor.calls, message="no frame arrived")

    # Send one DataTransfer of the app and answer it as the charger did.
    async def exchange(request_hex: str, answer_hex: str) -> None:
        data = json.dumps({"instructType": "a7", "originalData": request_hex})
        asked = asyncio.create_task(
            rig.vendor.command(
                "DataTransfer", {"vendorId": "READIAN", "messageId": "RemoteConfiguration", "data": data}
            )
        )
        call = await charger.next_hub_call()
        reply = json.dumps({"instructType": "TF", "originalData": answer_hex.upper()})
        await charger.answer(call, {"status": "Accepted", "data": reply})
        await asked

    await exchange(WRITE_REQUEST, WRITE_ACK)
    await eventually(lambda: rig.hub.listener.latest.app_plan == ((1, 43200, 50400), (2, 46800, 61200)), message="plan")
    await exchange(READ_REQUEST, EMPTY_ANSWER)
    await eventually(lambda: rig.hub.listener.latest.app_plan == (), message="plan not cleared")


# Intent: the weekday byte is a mask: Wednesday is written as 4, and a mask of 3 reads back as Monday and Tuesday.
# Why: the app showed a plan answered with day numbers one day off (Wednesday came out as Monday and Tuesday).
def test_the_weekday_byte_is_a_mask() -> None:
    wednesday = PlanWindow(3, 36000, 43200)

    assert encode_plan([wednesday])[4] == 4
    assert decode_plan(encode_plan([wednesday])) == [wednesday]
    assert decode_plan(
        bytes([1, 1, 1, 0, 3]) + (36000).to_bytes(4, "little") + (43200).to_bytes(4, "little") + b"\x20"
    ) == [
        PlanWindow(1, 36000, 43200),
        PlanWindow(2, 36000, 43200),
    ]
