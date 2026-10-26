"""Recognising the app's planning messages, answering them, and turning windows into blocks."""

from __future__ import annotations

import json

import pytest
from ocpp_hub_core import raedian
from ocpp_hub_core.plan_bridge import (
    PlanQuery,
    PlanUnreadable,
    PlanWrite,
    classify,
    plan_answer,
    windows_to_blocks,
    write_acknowledged,
)
from ocpp_hub_core.raedian import PlanWindow
from ocpp_hub_core.schedule_plan import Block

SESSION = bytes.fromhex("e33dde46e9b0fcb2")
MONDAY = PlanWindow(1, 43200, 50400)
TUESDAY = PlanWindow(2, 46800, 61200)


# A DataTransfer of the cloud that carries the given planning payload.
def _message(payload: bytes, command: int = raedian.COMMAND_PLAN) -> dict[str, object]:
    original = raedian.build_frame(raedian.HEAD_CLOUD, command, SESSION, payload)
    data = json.dumps({"instructType": f"{command:02x}", "originalData": original, "type": "RemoteConfiguration"})
    return {"vendorId": "READIAN", "messageId": "RemoteConfiguration", "data": data}


# Intent: a query, a write with understood windows, a write the hub cannot read, and a message that is not about the
# planning are told apart; only the first two are answered by the hub.
# Why: the hub takes over the planning only for what it understands completely; anything else must go its own way.
def test_planning_messages_are_told_apart() -> None:
    assert classify(_message(b"\x00")) == PlanQuery(SESSION)
    assert classify(_message(raedian.encode_plan([MONDAY, TUESDAY]))) == PlanWrite([MONDAY, TUESDAY], SESSION)
    assert isinstance(classify(_message(b"\x01\x01\x01")), PlanUnreadable)
    assert classify(_message(b"\x00", command=0xB1)) is None
    assert classify({"vendorId": "OTHER", "messageId": "x"}) is None
    assert classify({"vendorId": "READIAN", "messageId": "RemoteConfiguration", "data": "not json"}) is None


# Intent: the acknowledgement and the plan answer are frames of the charger in the session of the question, and the plan
# answer parses back to the windows it was built from.
# Why: the app only accepts an answer that carries its own session and a correct check byte.
def test_answers_carry_the_session_and_parse_back() -> None:
    ack = raedian.parse_frame(json.loads(write_acknowledged(SESSION)["data"])["originalData"].lower())
    answer = raedian.parse_frame(json.loads(plan_answer(SESSION, [MONDAY, TUESDAY])["data"])["originalData"].lower())

    assert (ack.head, ack.session, ack.payload) == (raedian.HEAD_CHARGER, SESSION, b"\x00")
    assert answer.session == SESSION and raedian.decode_plan(answer.payload) == [MONDAY, TUESDAY]


# Intent: windows become blocks per weekday, and a window that runs overnight is cut at midnight into the end of its day
# and the start of the next, Sunday running into Monday.
# Why: the schedule planner works in days; an overnight window that stayed whole would be refused or misplaced.
@pytest.mark.parametrize(
    ("windows", "blocks"),
    [
        ([MONDAY], {0: [Block(43200, 50400)]}),
        ([PlanWindow(1, 79200, 21600)], {0: [Block(79200, 86400)], 1: [Block(0, 21600)]}),
        ([PlanWindow(7, 79200, 21600)], {6: [Block(79200, 86400)], 0: [Block(0, 21600)]}),
    ],
)
def test_windows_become_blocks_with_overnight_cut_at_midnight(
    windows: list[PlanWindow], blocks: dict[int, list[Block]]
) -> None:
    assert windows_to_blocks(windows) == blocks


# A stand-in for the plan of the hub: it keeps what it is told and records what the mirror asks of it.
class FakePlan:
    # Start with a plan or without, and a record of the calls.
    def __init__(self, windows: list[PlanWindow] | None, accept: bool = True) -> None:
        self.windows = windows
        self.accept = accept
        self.written: list[list[PlanWindow]] = []
        self.imports: list[list[PlanWindow]] = []

    # The plan the hub holds.
    def plan(self, cp_id: str) -> list[PlanWindow] | None:
        return self.windows

    # The app wrote a plan: remember it unless this stand-in is set to refuse.
    async def app_wrote(self, cp_id: str, windows: list[PlanWindow]) -> bool:
        self.written.append(windows)
        if self.accept:
            self.windows = windows
        return self.accept

    # The hub learned the plan of the charger.
    def imported(self, cp_id: str, windows: list[PlanWindow]) -> None:
        self.imports.append(windows)
        self.windows = windows


# Send a DataTransfer of the cloud through the mirror with the given payload and return the answer frame of the vendor
# side, while an optional charger answer is given when the message reaches the charger.
async def _through(rig, charger, payload: bytes, reply: dict[str, object] | None = None) -> list:
    import asyncio

    asked = asyncio.create_task(rig.vendor.command("DataTransfer", _message(payload)))
    if reply is not None:
        call = await charger.next_hub_call()
        await charger.answer(call, reply)
    return await asked


# Intent: with a plan handler, an app write is taken over and acknowledged in its own session without reaching the
# charger, a refused write is answered Rejected, and a message about the planning that cannot be read is refused.
# Why: the app must see its write succeed only when the hub really applied it, and the charger's own planning must not
# be written behind the hub's back.
async def test_the_app_write_is_taken_over_and_not_passed_to_the_charger(start_rig) -> None:
    rig = await start_rig(commands="forward")
    plan = FakePlan([])
    rig.mirror._plan_handler = plan
    charger = await rig.hub.charger()
    await charger.boot()
    from tests.fake_vendor import eventually

    await eventually(lambda: rig.vendor.calls, message="no frame arrived")

    done = await _through(rig, charger, raedian.encode_plan([MONDAY, TUESDAY]))
    plan.accept = False
    refused = await _through(rig, charger, raedian.encode_plan([MONDAY]))
    unreadable = await _through(rig, charger, b"\x01\x01\x01")

    ack = raedian.parse_frame(json.loads(done[2]["data"])["originalData"].lower())
    assert plan.written[0] == [MONDAY, TUESDAY] and ack.session == SESSION and ack.payload == b"\x00"
    assert (
        refused[2] == {"status": "Rejected"} and unreadable[2] == {"status": "Rejected"} and charger.hub_calls.empty()
    )


# Intent: a query is answered from the plan the hub holds, and when the hub has no plan yet it goes to the charger and
# the charger's answer becomes the plan.
# Why: the app shows the plan of the hub once there is one, and the first answer of the charger seeds it.
async def test_the_app_query_is_answered_from_the_plan_or_seeded_from_the_charger(start_rig) -> None:
    from tests.fake_vendor import eventually

    rig = await start_rig(commands="forward")
    plan = FakePlan([MONDAY, TUESDAY])
    rig.mirror._plan_handler = plan
    charger = await rig.hub.charger()
    await charger.boot()
    await eventually(lambda: rig.vendor.calls, message="no frame arrived")

    answered = await _through(rig, charger, b"\x00")
    from_plan = raedian.decode_plan(
        raedian.parse_frame(json.loads(answered[2]["data"])["originalData"].lower()).payload
    )
    plan.windows = None
    charger_reply = plan_answer(SESSION, [PlanWindow(3, 3600, 7200)])
    seeded = await _through(rig, charger, b"\x00", charger_reply)

    assert from_plan == [MONDAY, TUESDAY] and charger.hub_calls.empty()
    assert seeded[2]["status"] == "Accepted" and plan.imports == [[PlanWindow(3, 3600, 7200)]]
