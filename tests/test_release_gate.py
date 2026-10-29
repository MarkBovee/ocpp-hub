"""The release gate: which checks a release needs and how their results are judged."""

from __future__ import annotations

from tests.tool_loader import load_tool

gate = load_tool("release_gate")


# Intent: build a minimal check-run record the way the GitHub API returns it.
# Why: the gate reads only name, status and conclusion.
def _run(name: str, status: str = "completed", conclusion: str | None = "success") -> dict[str, object]:
    return {"name": name, "status": status, "conclusion": conclusion}


# Intent: a private repository is gated on hassfest alone, a public one also on HACS.
# Why: the HACS action cannot run on a private repository, so requiring it there would block every release.
def test_required_checks_depend_on_visibility() -> None:
    assert gate.required_checks(private=True) == ("validate",)
    assert gate.required_checks(private=False) == ("validate", "validate-hacs")


# Intent: a missing or unfinished required check waits, a finished failure fails at once, all green passes.
# Why: hassfest can finish after the tag push, but a failed check must never be waited out.
def test_evaluate_waits_fails_or_passes() -> None:
    assert gate.evaluate([_run("validate")], ("validate",)) == ("pass", "all required checks succeeded")
    assert gate.evaluate([], ("validate",))[0] == "pending"
    assert gate.evaluate([_run("validate", status="in_progress", conclusion=None)], ("validate",))[0] == "pending"
    assert gate.evaluate([_run("validate", conclusion="failure")], ("validate",))[0] == "fail"


# Intent: the polling loop honours the required list it is given.
# Why: with a private repository the missing HACS run must not keep the gate pending until the timeout.
def test_wait_for_gate_uses_the_required_checks() -> None:
    ok, _ = gate.wait_for_gate(lambda: [_run("validate")], timeout=1.0, required=gate.required_checks(private=True))

    assert ok


# Intent: when a check ran twice on a commit, the newest run decides, whatever order the API lists them in.
# Why: the first public tag failed because a HACS run skipped while the repository was private was read instead of the
# successful run made after it went public.
def test_the_newest_run_of_a_check_decides() -> None:
    skipped = {**_run("validate-hacs", conclusion="skipped"), "id": 1}
    passed = {**_run("validate-hacs"), "id": 2}
    validate = {**_run("validate"), "id": 3}

    assert gate.evaluate([passed, skipped, validate])[0] == "pass"
    assert gate.evaluate([{**passed, "id": 0}, skipped, validate])[0] == "fail"
