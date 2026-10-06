"""PB-6 — Backbone invocation order verification for RET-C2-577.

Asserts that Graph().invoke() over a SUCCESS-yielding payload executes
backbone slots in the required order:
  InitializeNode -> PreProcessNode -> <main-slot> -> PostProcessNode -> FinalizeNode

Two template-specific fields (the ONLY parts that change per template):
"""

import json
import os


# ── Template-specific ─────────────────────────────────────────────────────────

# Class name of the `main`-slot node in src/graph/graph.py
_MAIN_SLOT_NODE = "DeliveryStatusGraphNode"

# A non-empty query that yields AgentStatus.SUCCESS through the full pipeline.
# Must be < 2000 chars and carry no disallowed instruction.
_VALID_PAYLOAD = "My order TRK-001 hasn't updated since yesterday. Where is it and will it arrive today?"

# ── Boilerplate ───────────────────────────────────────────────────────────────

_EXPECTED_BACKBONE = [
    "InitializeNode",
    "PreProcessNode",
    _MAIN_SLOT_NODE,
    "PostProcessNode",
    "FinalizeNode",
]


def _run(input_text: str):
    """Invoke the agent with VERIFIED_EXTERNAL trust (never for_internal()).

    PB-6 rule: passing InvocationContext.for_internal() masks the inner-node
    trust-trap (inner ANONYMOUS vs outer VERIFIED_EXTERNAL) — always use
    TrustLevel.VERIFIED_EXTERNAL here to exercise the real external path.
    """
    from src.graph.graph import Graph
    from framework.schemas.invocation_context import InvocationContext
    from framework.schemas.trust_level import TrustLevel

    agent = Graph()
    agent.compile()
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
    return agent.invoke(input_text, ctx=ctx)


def _history_names(result: dict) -> list[str]:
    """Extract string class names from node_history entries."""
    history = result.get("node_history", [])
    return [(h if isinstance(h, str) else getattr(h, "__name__", type(h).__name__)) for h in history]


class TestPbInvokeOrder:
    """PB-6: backbone order must be Init → Pre → Main → Post → Finalize on SUCCESS."""

    def test_success_payload_yields_success_status(self):
        """_VALID_PAYLOAD must reach AgentStatus.SUCCESS end-to-end."""
        result = _run(_VALID_PAYLOAD)
        from framework.schemas.agent_status import AgentStatus

        status = result.get("status")
        assert status in (AgentStatus.SUCCESS, AgentStatus.SUCCESS.value, "SUCCESS"), (
            f"_VALID_PAYLOAD must yield SUCCESS; got status={status!r}. "
            "A non-SUCCESS status short-circuits main->finalize and skips post_process."
        )

    def test_backbone_contains_all_five_slots(self):
        """node_history must reference all 5 backbone class names."""
        result = _run(_VALID_PAYLOAD)
        names = _history_names(result)
        for slot in _EXPECTED_BACKBONE:
            assert any(slot in n for n in names), f"Backbone slot '{slot}' missing from node_history. Got: {names}"

    def test_backbone_order_pre_before_main_before_post(self):
        """PreProcessNode must precede main-slot, which must precede PostProcessNode."""
        result = _run(_VALID_PAYLOAD)
        names = _history_names(result)
        positions = {}
        for slot in ("PreProcessNode", _MAIN_SLOT_NODE, "PostProcessNode"):
            hits = [i for i, n in enumerate(names) if slot in n]
            assert hits, f"Expected '{slot}' in node_history. Got: {names}"
            positions[slot] = hits[0]
        assert (
            positions["PreProcessNode"] < positions[_MAIN_SLOT_NODE]
        ), "PreProcessNode must run before the main-slot node"
        assert (
            positions[_MAIN_SLOT_NODE] < positions["PostProcessNode"]
        ), "Main-slot node must run before PostProcessNode"

    def test_verified_external_caller_admitted(self):
        """External callers (VERIFIED_EXTERNAL) must not be denied by any backbone node."""
        from framework.schemas.invocation_context import InvocationContext
        from framework.schemas.trust_level import TrustLevel
        from src.graph.graph import Graph
        from framework.schemas.agent_status import AgentStatus

        agent = Graph()
        agent.compile()
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = agent.invoke(_VALID_PAYLOAD, ctx=ctx)
        status = result.get("status")
        assert status in (AgentStatus.SUCCESS, AgentStatus.SUCCESS.value, "SUCCESS"), (
            "VERIFIED_EXTERNAL caller must not be denied. "
            "Check that inner domain nodes declare TrustLevel.ANONYMOUS."
        )

    def test_formatted_output_present_on_success(self):
        """A non-empty agent output must be present when status is SUCCESS.

        The framework surfaces the final response via the AgentState 'output'
        field (set by FinalizeNode); 'formatted_output' (State subclass field)
        may additionally carry the same text but is not guaranteed by the
        framework's invoke() filter.  Either being non-empty satisfies the PB-6
        "agent produces a response" invariant.
        """
        result = _run(_VALID_PAYLOAD)
        from framework.schemas.agent_status import AgentStatus

        if result.get("status") in (AgentStatus.SUCCESS, AgentStatus.SUCCESS.value, "SUCCESS"):
            output_text = result.get("output") or result.get("formatted_output")
            assert output_text, (
                "output (or formatted_output) must not be empty on SUCCESS — "
                f"output={result.get('output')!r}, "
                f"formatted_output={result.get('formatted_output')!r}"
            )

    def test_the_committed_deploy_payload_is_this_same_request(self):
        """The deployment smoke check posts deploy/invoke_payload.json verbatim.

        Nothing used to hold those two together, and a payload the entry node
        refuses produces a well-formed 200 with an agent-level error inside it —
        every surrounding assertion passes and the only record is one line in an
        artifact nobody opens. Taking the payload from this fixture rather than
        writing one for the deployment means the two cannot drift apart.
        """
        payload_path = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "invoke_payload.json")
        with open(payload_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        assert (
            payload["input"] == _VALID_PAYLOAD
        ), "deploy/invoke_payload.json has drifted from the request this test proves is answerable"

    def test_the_committed_deploy_payload_is_actually_answered(self):
        """And the request itself succeeds, which is the property that matters."""
        payload_path = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "invoke_payload.json")
        with open(payload_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        from framework.schemas.agent_status import AgentStatus

        result = _run(payload["input"])
        assert result.get("status") in (AgentStatus.SUCCESS, AgentStatus.SUCCESS.value, "SUCCESS")
        assert result.get("output")
