"""AgentCore Platform v1.0"""

# RET-C2-577 — DomainWorkflowGraph (inner BaseGraph)
#
# Inner Cat 2 graph: encapsulates the full last-mile delivery status Q&A
# domain workflow. Called by DeliveryStatusGraphNode.get_subgraph()
# in graph.py (outer AgentBaseGraph).
#
# Pipeline (linear):
#   START -> query_normalize -> order_context_extract -> status_pattern_retrieve
#         -> response_synthesize -> escalation_check -> output_format -> END
#
# Rules enforced:
#   ✅ Inherits BaseGraph (fully custom topology)
#   ✅ Implements all 7 BaseGraph ABC methods
#   ✅ register_nodes() does NOT call super() (abstract in BaseGraph)
#   ✅ Does NOT register initialize / finalize (outer backbone concerns)
#   ✅ get_output() designed together with DeliveryStatusGraphNode.merge_output()
#   ✅ All inner domain nodes: required_trust_level = TrustLevel.ANONYMOUS
#   ❌ No agenticstar imports
#   ❌ Not placed under src/subagents/

import json
from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import get_delivery_contract
from src.nodes.escalation_check_node import EscalationCheckNode
from src.nodes.order_context_extract_node import OrderContextExtractNode
from src.nodes.output_format_node import OutputFormatNode
from src.nodes.query_normalize_node import QueryNormalizeNode
from src.nodes.response_synthesize_node import ResponseSynthesizeNode
from src.nodes.status_pattern_retrieve_node import StatusPatternRetrieveNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for RET-C2-577.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by DeliveryStatusGraphNode.get_subgraph() in graph.py.

    Pipeline (linear — no conditional branching):
        START
          -> query_normalize         (QueryNormalizeNode)
          -> order_context_extract   (OrderContextExtractNode)
          -> status_pattern_retrieve (StatusPatternRetrieveNode)
          -> response_synthesize     (ResponseSynthesizeNode)
          -> escalation_check        (EscalationCheckNode)
          -> output_format           (OutputFormatNode)
          -> END

    All nodes are FunctionNode subclasses with required_trust_level = ANONYMOUS.
    get_output() shapes the sub_result dict consumed by DeliveryStatusGraphNode.merge_output().
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "ret_c2_577_delivery_status_workflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """Validate inner graph config before compilation.

        Every value has already been through the bounded parser in
        src/services/runtime_config.py, which is the only reader of
        config/config.yaml. Re-deriving the same bounds here would give the
        repository two places to disagree about what a valid threshold is, so
        this checks the shape it was handed and nothing more.
        """
        if self.config and not isinstance(self.config, dict):
            raise TypeError(f"{type(self).__name__}: config must be a mapping")

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the inner state with the caller contract and the live tuning.

        Two things the framework does not carry across the graph boundary:

        The caller's structured parameters. ``GraphNode.execute()`` invokes the
        subgraph with the request STRING only, so the validated delivery records
        travel on the ContextVar bridge, set by the outer node one step before
        this hook runs.

        The runtime configuration. Domain ``execute()`` methods receive no config
        argument, so seeding state is the only route a declared value has into a
        node. Every key here is read by a node below; none is decorative.
        """
        config = self.config or {}
        contract = get_delivery_contract()
        return {
            # Serialised, like every other structured field in State: the flat
            # TypedDict is checkpointed with msgpack and a plain string round-trips
            # where a nested mapping is where corruption starts.
            "delivery_contract": json.dumps(contract, ensure_ascii=False),
            "escalation_threshold_days": float(config.get("escalation_threshold_days", 3.0)),
            "max_kb_chunks": int(config.get("max_kb_chunks", 4)),
            "output_channel": str(contract.get("channel") or config.get("default_channel", "line")),
            "carrier_scope_config": json.dumps(list(config.get("carrier_scope", [])), ensure_ascii=False),
        }

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all 6 domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.
        Every key registered here is referenced in add_edges().

        Nodes are instantiated with NO constructor arguments. Configuration
        reaches them through seeded state (_extra_initial_state), not through a
        per-call argument: the framework's node wrapper calls execute(state) with
        one argument, so a node that declared `config=None` would read its
        default on every single invocation.
        """
        self._nodes["query_normalize"] = QueryNormalizeNode()
        self._nodes["order_context_extract"] = OrderContextExtractNode()
        self._nodes["status_pattern_retrieve"] = StatusPatternRetrieveNode()
        self._nodes["response_synthesize"] = ResponseSynthesizeNode()
        self._nodes["escalation_check"] = EscalationCheckNode()
        self._nodes["output_format"] = OutputFormatNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear delivery-status domain topology.

        All edges are unconditional; the VectorRAG pipeline is linear.
        route() is implemented to satisfy the ABC contract but is never
        called (no add_conditional_edges() usage).
        """
        self._sg.add_edge(START, "query_normalize")
        self._sg.add_edge("query_normalize", "order_context_extract")
        self._sg.add_edge("order_context_extract", "status_pattern_retrieve")
        self._sg.add_edge("status_pattern_retrieve", "response_synthesize")
        self._sg.add_edge("response_synthesize", "escalation_check")
        self._sg.add_edge("escalation_check", "output_format")
        self._sg.add_edge("output_format", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: AgentState) -> str:
        """Conditional routing — required by BaseGraph ABC.

        This topology is purely linear; add_conditional_edges() is not used,
        so this method is never called at runtime. Returns END on ERROR to
        make an unexpected call safe.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "output_format"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the output dict returned to DeliveryStatusGraphNode as sub_result.

        Designed together with DeliveryStatusGraphNode.merge_output() in graph.py:
            Inner get_output()   emits: "output", "status", "escalation_*", ...
            Outer merge_output() reads: sub_result.get("output"),
                                        sub_result.get("status")

        Additional fields are included for observability. merge_output() only
        maps "output" + "status" into the outer state; the rest are available
        for future extension without an inner-graph change.
        """
        return {
            "output": state.get("result"),
            "status": state.get("status"),
            "escalation_required": state.get("escalation_required", False),
            "escalation_reason": state.get("escalation_reason"),
            "delivery_options": state.get("delivery_options"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
