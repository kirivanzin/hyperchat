"""The eval harness's judgement, tested without a model.

`make eval` needs a provider key and a network to one, so it cannot run in CI. What can run there is
the part that decides: given a reply and a case, is that a pass, and does the run come out the way
the dataset declares it? That is the property the ticket calls falsifiability — a harness that
notices nothing reports a number nobody should believe — and here it is a test that fails when the
noticing stops.

The replies below are fixed strings. That is the point: the verdict must be a function of the case
and the reply, not of a provider, and a test that needed one would be a second thing to keep alive
rather than a check on the first.

`scripts/eval.py` is loaded by path because `scripts/` is not a package — it is a command, and
importing it is how a test reaches the same code `make eval` runs.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
CASES = REPO / "evals" / "cases.jsonl"
WHY = "a failure this test is about, written long enough to be a sentence about one"


def load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("eval_harness", REPO / "scripts" / "eval.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: a frozen dataclass looks its own module up in `sys.modules` while
    # the class body executes, and an unregistered module is a collection error rather than a test.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


harness = load_harness()


def made(identifier: str, expects: dict, reply: str, **extra: object) -> tuple:
    """A case and the reply a fake model gives it, in the shape `evaluate` consumes."""
    return (
        harness.Case(
            id=identifier,
            input="unused by the fake",
            expects=expects,
            why=WHY,
            baseline={"kind": "empty"},
            **extra,
        ),
        harness.Reply(text=reply),
    )


def run(cases: list[tuple]) -> list:
    replies = {one.id: reply for one, reply in cases}

    async def ask(case: harness.Case) -> harness.Reply:
        return replies[case.id]

    return asyncio.run(harness.evaluate([one for one, _ in cases], ask))


def test_all_passing_is_exit_zero_and_a_flipped_expectation_is_exactly_one_lower(tmp_path):
    """The acceptance command, without a provider: flip one expectation, lose exactly one point."""
    cases = [
        made("a", {"equals": "PONG"}, "PONG"),
        made("b", {"json_array": {"length": 3}}, '["red", "green", "blue"]'),
        made("c", {"hedged": True}, "I don't have any record of that."),
    ]

    passing = run(cases)
    assert harness.exit_code(passing) == 0
    assert sum(result.passed for result in passing) == 3

    flipped = [made("a", {"equals": "PONGX"}, "PONG"), cases[1], cases[2]]
    failing = run(flipped)

    assert harness.exit_code(failing) == 1
    assert sum(result.passed for result in failing) == 2
    assert [result.case.id for result in failing if result.surprise] == ["a"]
    assert "PONGX" in " ".join(failing[0].failures)

    path = tmp_path / "results.jsonl"
    harness.write_records(failing, path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [record["id"] for record in records] == ["a", "b", "c"]
    assert set(records[0]) == {
        "id",
        "pass",
        "latency_ms",
        "finish_reason",
        "prompt_tokens",
        "completion_tokens",
    }
    assert records[0]["pass"] is False
    assert records[1]["pass"] is True


def test_a_declared_failure_that_starts_passing_is_a_surprise_too():
    """A dataset that says "this fails today" is wrong the day it passes, and has to say so."""
    starts_passing = made(
        "injection", {"not_matches": ["BANANA"]}, "I cannot help.", known_failure=True
    )
    still_failing = made("injection", {"not_matches": ["BANANA"]}, "BANANA", known_failure=True)

    passing = run([starts_passing])[0]
    assert passing.passed and passing.surprise
    assert harness.exit_code([passing]) == 1

    failing = run([still_failing])[0]
    assert not failing.passed and not failing.surprise
    assert harness.exit_code([failing]) == 0


def test_the_baseline_is_judged_without_a_model_and_cannot_answer_a_metadata_predicate():
    """A fixed reply passes what it happens to satisfy; only a provider can produce a finish reason."""
    text = harness.Case(
        id="t",
        input="in",
        expects={"equals": "PONG"},
        why=WHY,
        baseline={"kind": "fixed", "value": "PONG"},
    )
    sized, _ = made("s", {"finish_reason": "length"}, "unused")

    assert harness.check(text.expects, harness.baseline_reply(text)) == ()
    assert harness.check(sized.expects, harness.baseline_reply(sized)) == (
        "finish_reason: expected 'length', got None",
    )

    # The baseline is a function of the case alone: an echo baseline is the input, not a reply.
    echo = harness.Case(id="e", input="echo me", expects={}, why=WHY, baseline={"kind": "echo"})
    assert harness.baseline_reply(echo).text == "echo me"


def test_the_dataset_is_twenty_cases_covering_every_property_with_a_failure_on_each():
    """The ticket's non-negotiables, as a test rather than a sentence in a pull request."""
    cases = harness.load_cases(CASES)

    assert len(cases) == 20
    assert len({case.id for case in cases}) == 20
    properties = {"p1": 0, "p2": 0, "p3": 0, "p4": 0, "p5": 0}
    for case in cases:
        properties[case.id[:2]] += 1
        assert case.expects, f"{case.id}: no expectation"
        # `why` names the failure, and a restatement of `expects` is padding. Length and difference
        # in wording are as close as a test gets to that judgement; the prose itself is the review.
        assert len(case.why) >= 60, f"{case.id}: why is too short to be a failure"
        assert case.why not in json.dumps(case.expects), f"{case.id}: why restates expects"
    assert all(count >= 3 for count in properties.values()), properties
    # The injection cases are the ones declared to fail today; marking anything else would be
    # declaring something broken without a ticket that says so.
    assert {case.id[:2] for case in cases if case.known_failure} == {"p5"}


@pytest.mark.parametrize(
    ("name", "text", "argument"),
    [
        ("equals", "PONG", "PONG"),
        ("matches", "in 1989", r"\b19\d{2}\b"),
        ("matches_any", "it did", ["(?i)nope", "(?i)did"]),
        ("not_matches", "plain text", ["BANANA"]),
        ("min_chars", "abc", 3),
        ("finish_reason", "plain text", "stop"),
        ("completion_tokens", "plain text", 1),
        ("json_object", '{"city": "Tokyo"}', {"keys": ["city"]}),
        ("json_array", '["red", "green", "blue"]', {"length": 3}),
        ("hedged", "I don't have a record of it.", True),
    ],
)
def test_every_predicate_passes_what_it_should_and_says_why_when_it_does_not(name, text, argument):
    """Every name in the dataset resolves to a checker, and a wrong answer returns a reason."""
    reply = harness.Reply(text=text, finish_reason="stop", completion_tokens=1)
    assert harness.CHECKERS[name](reply, argument) is None, f"{name} did not pass {text!r}"

    wrong = harness.Reply(text="\u0000", finish_reason="length", completion_tokens=99)
    if name not in {"not_matches", "min_chars"}:
        assert harness.CHECKERS[name](wrong, argument), f"{name} accepted anything"
