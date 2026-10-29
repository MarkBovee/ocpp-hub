"""tools/raedian_frames.py: pull Raedian frames out of a hub log, pair them and flag the commands that are new."""

from __future__ import annotations

from tests.tool_loader import load_tool

frames = load_tool("raedian_frames")

# Lines as the hub logged them on 2026-10-26 (newest first, as the HA log API returns them): the request of the cloud
# and the answer of the charger for command b0, and a refused planning write.
LOG = "\n".join(
    [
        "2026-10-26 09:51:35.256 INFO [forward] DataTransfer answer of CP: "
        + """{'status': 'Accepted', 'data': '{"originalData": "AAB00000040008700123456789ABCDBE9614DF6A"}'}""",
        "2026-10-26 09:51:35.256 INFO [forward] DataTransfer request for CP: "
        + """{'data': '{"instructType":"b0","originalData":"feb00000050008280123456789abcdbe9614df6a0d"}'}""",
        "2026-10-26 10:04:27.718 WARNING [plan] refused: x; it said "
        + """{"instructType":"a7","originalData":"fea700001a0008950123456789abcdbe"""
        + """0102000004a08c0000c0a8000020010008e0c4000000e1000020"}""",
        "not a frame line",
    ]
)


# Intent: the frames of a log are found, the request and the answer of one command are paired whichever way round the
# log runs, and the unknown command is marked NEW while the planning is known.
# Why: this is how a future session reads an unknown command from a log without going through the lines by hand.
def test_frames_are_found_paired_and_marked() -> None:
    exchanges = frames.pair_exchanges(frames.find_frames(LOG))
    by_command = {exchange.command: exchange for exchange in exchanges}

    assert sorted(by_command) == [0xA7, 0xB0]
    assert by_command[0xB0].request.frame.payload.hex() == "9614df6a0d"
    assert by_command[0xB0].answer.frame.payload.hex() == "9614df6a"
    assert by_command[0xA7].answer is None
    report = frames.report(exchanges, unknown_only=True)
    assert "command b0  [NEW]" in report and "a7" not in report
