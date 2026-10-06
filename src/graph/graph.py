"""AgentCore Platform v1.0"""

# RET-C2-577 — outer graph.
#
# Retail last-mile delivery-status question answering.
#
# Architecture (a nested Cat 2 template):
#
#   Outer backbone (fixed — add_edges() is NOT overridden):
#     START -> initialize -> pre_process -> main -> {route} -> post_process
#           -> finalize -> END
#
#   The `main` slot is a GraphNode subclass (DeliveryStatusGraphNode) that
#   delegates the whole delivery-status workflow to DomainWorkflowGraph, so the
#   domain topology stays inside the inner graph and the backbone is untouched.
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph (6-node domain topology)
#   src/graph/context_bridge.py        <- validated caller contract across the boundary
#
# Rules enforced here:
#   - Graph inherits AgentBaseGraph (framework base class, directly)
#   - super().register_nodes() is called first (fills initialize + finalize)
#   - DeliveryStatusGraphNode occupies self._nodes["main"]
#   - merge_output() returns only changed keys
#   - add_edges() is NOT overridden
#   - No platform SDK imports

import json
from typing import Any, ClassVar, Dict

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import set_delivery_contract
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State
from src.services.runtime_config import domain_config, runtime_config


class DeliveryStatusGraphNode(GraphNode):
    """The `main` slot: wraps the inner delivery-status workflow.

    Contracts:
      get_subgraph()    - instantiate DomainWorkflowGraph with the forwarded
                          runtime config (_parent_config())
      extract_input()   - hand the request string to the inner graph and stash
                          the validated caller contract on the bridge
      merge_output()    - map sub_result fields into the outer state delta
      error_strategy    - "propagate": re-raise inner errors (fail fast)
    """

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    # Set by the outer graph at registration, from the config the graph was
    # CONSTRUCTED with. Left as None only when this node is built on its own,
    # in which case the file is read directly.
    #
    # Reading the file here unconditionally is the mistake this attribute exists
    # to avoid, and it is a subtle one: it looks live, because the file really is
    # read — but the config the registry passes to Graph(config=…) would be
    # ignored, so a deployment that configures the agent through the registry
    # would have no effect on the pipeline. The dead-config defect this migration
    # fixed, in a new place. Caught by a mutation run.
    delivery_config: "Dict[str, Any] | None" = None

    def _parent_config(self) -> Dict[str, Any]:
        """The live delivery tuning, forwarded to the inner graph.

        Values come from config/config.yaml through the one validated reader,
        never from the static manifest: the flat manifest carries identity and
        compile-time requirements only, so a reader pointed at it returns nothing
        and the pipeline degrades to node defaults with no error anywhere.

        This used to return a hard-coded empty dict. Everything config/config.yaml
        declared — the escalation threshold, the passage cap, the default channel
        — was replaced by a literal written inside extract_input(), so editing the
        file changed nothing about the answer.
        """
        if self.delivery_config is not None:
            return dict(self.delivery_config)
        return domain_config(runtime_config())

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner delivery workflow graph.

        Imported inside the method to avoid circular-import risk at module load
        time. The inner graph receives the runtime-derived config through its
        constructor; its domain nodes still take no constructor arguments and
        read their tuning per call from seeded state.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the request string, and bridge the validated caller contract.

        The framework hands only a string to the inner graph, so the structured
        part of the request travels on the bridge instead — set here, one step
        before the inner invoke, and read by the inner graph's initial-state
        hook. Only the contract the pre_process node already validated crosses.
        """
        raw = state.get("delivery_contract")
        contract: Dict[str, Any] = {}
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = {}
            if isinstance(parsed, dict):
                contract = parsed
        set_delivery_contract(contract)
        return str(state.get("validated_input") or state.get("user_input", ""))

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map the inner result into the outer state delta (changed keys only).

        Key coupling, designed together with DomainWorkflowGraph.get_output():
          inner get_output() emits  -> "output", "status", "escalation_*"
          this merge_output() reads -> sub_result.get("output"), .get("status")

        The answer is mapped to "result" because the post_process output gate
        reads state["result"]; without that mapping the surfaced output would
        always be empty.

        A non-success inner status never reaches here (error_strategy is
        "propagate", so the inner error is re-raised first), but the guard is
        kept so a future strategy change cannot start publishing an un-gated
        answer through this path.
        """
        status = sub_result.get("status")
        if status != AgentStatus.SUCCESS.value:
            return {"result": None, "status": status}
        return {
            "result": sub_result.get("output"),
            "status": status,
            "escalation_required": bool(sub_result.get("escalation_required")),
            "delivery_options": sub_result.get("delivery_options"),
        }


class Graph(AgentBaseGraph):
    """Outer graph for RET-C2-577.

    Inherits AgentBaseGraph directly (framework base class). The domain logic is
    fully encapsulated in DeliveryStatusGraphNode (the `main` slot), which
    delegates to DomainWorkflowGraph.

    Backbone (fixed):
        START -> initialize -> pre_process -> main -> post_process -> finalize -> END

    register_nodes() is the only override:
      - super().register_nodes() fills initialize and finalize
      - pre_process:  PreProcessNode  (caller contract validation + request screen)
      - main:         DeliveryStatusGraphNode
      - post_process: PostProcessNode (output gate)

    add_edges() is NOT overridden — backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        return "RetailLastMileDeliveryStatusQAAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all five backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default initialize node (schema version, session id, trust
        level) and finalize node (response metadata, total time).
        """
        super().register_nodes()  # fills: initialize, finalize

        # The config this graph was CONSTRUCTED with is what the pipeline runs
        # on. The registry loads config/config.yaml and passes it here; the
        # standalone entry point does the same. Falling back to reading the file
        # inside the node would make the constructor argument decorative — which
        # is the same defect as the empty dict it replaced, one level along.
        main = DeliveryStatusGraphNode()
        main.delivery_config = domain_config(self.config or runtime_config())

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = main
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the caller-facing envelope.

        The framework's envelope resolves the output as
        ``formatted_output or result`` with NO status check, so an error path
        that left ``result`` in place ships the un-gated answer inside the error
        envelope. The output gate cannot close that on its own: when the
        framework's own credential scan raises on the gate node's return value,
        the wrapper discards that node's whole delta — the clearing included —
        and ``result`` survives untouched in state.

        So on any non-success status the output resolves to the gate's own
        notice, or to None. It never falls back to ``result``.

        No third layer re-scans the success path here. One would contain a leak
        by itself and thereby make the gate node's own scan unfalsifiable; the
        gate is the single place the success path is checked, and the mutant
        that removes it has to be able to fail.
        """
        output: Dict[str, Any] = dict(super().get_output(state))
        if state.get("status") != AgentStatus.SUCCESS.value:
            output["output"] = state.get("formatted_output") or None
        return output
