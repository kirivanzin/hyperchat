"""Whether runs are traced, where they go, and how much of them is kept.

A tracing configuration that is wrong is silent. The service answers 200, the project view stays
empty, and nothing anywhere says why — which is the one failure in this repository that does not
announce itself. These are the pairs that say it, on the configuration line the service and the
harness already write, because two places to read one fact is how they disagree.

**Report-only.** Nothing here enables or disables tracing, and nothing here can fail a request: a
missing key, an unreachable endpoint or an ambiguous destination is a line in the log, not a 500.
Observability is not a gate.

**Asked of langsmith, not re-derived.** `tracing_is_enabled`, `get_env_var` and `resolve_destination`
are the functions the tracer itself consults, so the report cannot drift from the decision it
reports. The verdict — on, or off — is always langsmith's; what this module adds is the shape of the
failure, because "off" on its own is the answer that explains nothing.

Four shapes, and every one of them is otherwise invisible:

- **off**, because `LANGSMITH_TRACING` is not true;
- **on with no API key** — runs are built and never sent, which looks exactly like tracing working
  until somebody opens the project;
- **an agent address langsmith cannot use** — half of the `LANGSMITH_AGENT_*` pair, or an environment
  that is not one of the four it accepts;
- **a project and an agent address both named**, which is a contradiction rather than a preference:
  langsmith warns once and then traces nothing for any call that does not name a destination in code.

The input and output flags are reported too, and for the reason the deployment records them: they are
read by the client from `HIDE_INPUTS` / `HIDE_OUTPUTS`, so "what is in the trace" is a property of
this configuration and not a detail nobody checks. The client compares each against the string
`"true"`, and so does this — a truthier spelling would make the line disagree with what is sent.
"""

from __future__ import annotations

from langsmith import utils
from langsmith.address import EnvAddressError, address_from_env, resolve_destination

# The variables a tracing decision can read. A test clears them for the same reason langsmith's cache
# has to be cleared: a case that inherits the previous case's environment agrees with the test rather
# than with the code.
VARIABLES = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGCHAIN_PROJECT",
    "LANGSMITH_AGENT_ID",
    "LANGSMITH_AGENT_ENVIRONMENT",
    "LANGSMITH_HIDE_INPUTS",
    "LANGSMITH_HIDE_OUTPUTS",
)


def _hidden(name: str) -> bool:
    """Whether langsmith will drop this part of a run. `"true"`, as the client compares it."""
    return utils.get_env_var(name, default=None) == "true"


def fields() -> dict[str, str]:
    """The tracing facts as pairs for the configuration line, in the order they are read.

    A mapping rather than a sentence because the line is a list of pairs: a value with spaces in it
    would split into tokens that look like configuration, and `inputs=hidden` is a fact somebody will
    want to grep for by name.
    """
    if not utils.tracing_is_enabled():
        return {"tracing": "off", "tracing_why": "LANGSMITH_TRACING-is-not-true"}

    visibility = {
        "tracing_inputs": "hidden" if _hidden("HIDE_INPUTS") else "visible",
        "tracing_outputs": "hidden" if _hidden("HIDE_OUTPUTS") else "visible",
    }
    project = utils.get_env_var("PROJECT", default=None)

    try:
        destination, address = resolve_destination()
    except EnvAddressError:
        # langsmith has decided that this configuration names no destination. Which of the two
        # failures it is decides what the operator does next, so it is named: `address_from_env` is
        # asked only for that, never instead of the verdict above.
        try:
            complete = address_from_env() is not None
        except EnvAddressError:
            complete = False
        if complete and project:
            why = "a-project-and-an-agent-address-are-both-set"
        else:
            why = "the-agent-address-is-incomplete-or-invalid"
        return {"tracing": "off", "tracing_why": why}

    where = (
        {"tracing_agent": f"{address['id']}/{address['environment'].lower()}"}
        if address is not None
        else {"tracing_project": destination or "default"}
    )
    if not utils.get_env_var("API_KEY", default=None):
        # On, and going nowhere: the one shape that reads as healthy from every endpoint and leaves an
        # empty project view. The key is never printed, only whether one exists.
        return {"tracing": "on", **where, **visibility, "tracing_api_key": "unset"}
    return {"tracing": "on", **where, **visibility}
