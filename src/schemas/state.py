"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never a Pydantic model. LangGraph checkpoints
# are serialised with msgpack, and a model instance there corrupts silently.
# Extend AgentState with agent-specific fields only. No credentials, no secrets.
#
# RET-C2-577 — Retail Last-Mile Delivery Status Q&A Agent.
# A nested Cat 2 graph: an outer backbone (AgentBaseGraph) around an inner
# domain workflow (BaseGraph). The fields below cover both layers.
#
# Nothing here holds a recipient's name, address or contact detail. That is a
# property of the caller contract rather than of a filter: the contract declares
# no field a caller could put one in, and every value that renders is either a
# closed-set code, a bounded number, or an identifier on the inert alphabet. The
# output boundary in PostProcessNode is the second line, not the first.

from typing import Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Flat TypedDict for RET-C2-577.

    Shared fields (user_input, input_context, status, session_id, node_history,
    error_log, validated_input, result, formatted_output, …) are inherited from
    AgentState.

    Inner domain pipeline, in order:
      QueryNormalize -> OrderContextExtract -> StatusPatternRetrieve ->
      ResponseSynthesize -> EscalationCheck -> OutputFormat
    """

    # ------------------------------------------------------------------
    # Caller contract — written by PreProcessNode, carried across the graph
    # boundary by src/graph/context_bridge.py
    # ------------------------------------------------------------------

    # JSON object: the validated caller contract (orders, channel). Serialised
    # because a nested mapping is what msgpack checkpointing corrupts.
    delivery_contract: Optional[str]

    # ------------------------------------------------------------------
    # Runtime configuration — seeded from config/config.yaml by the inner
    # graph's _extra_initial_state(); node execute() methods take no config
    # argument, so state is the only route a declared value has into a node.
    # ------------------------------------------------------------------

    # Days overdue at which the answer routes the customer to a person.
    escalation_threshold_days: float

    # Upper bound on delivery-knowledge passages quoted in one answer.
    max_kb_chunks: int

    # Channel the answer is rendered for: line | chat | email.
    output_channel: Optional[str]

    # JSON array of the carrier identifiers this deployment answers for.
    carrier_scope_config: Optional[str]

    # ------------------------------------------------------------------
    # QueryNormalizeNode
    # ------------------------------------------------------------------

    # The question, lower-cased and whitespace-collapsed.
    normalized_query: Optional[str]

    # The parcel reference, folded onto the contract's inert alphabet. This is
    # both the key the delivery records are matched on and the only form echoed
    # back, so what the customer sees is what the lookup used.
    order_reference: Optional[str]

    # Carrier detected in the question, when it is one this deployment serves.
    carrier_hint: Optional[str]

    # ------------------------------------------------------------------
    # OrderContextExtractNode
    # ------------------------------------------------------------------

    # Where the delivery context came from: caller_record | baseline. The
    # difference between an answer and a guess, so the answer states it.
    order_context_source: Optional[str]

    # Delivery status: IN_TRANSIT | DELAYED | DELIVERED | UNKNOWN.
    order_status_code: Optional[str]

    # Description of the last scan event, from this repository's own catalogue.
    order_last_event: Optional[str]

    # Days past the expected delivery date. Finite and bounded by the contract.
    order_delay_days: float

    # Human-readable arrival estimate.
    estimated_arrival: Optional[str]

    # True when the last scan event always warrants a person (loss, damage).
    escalating_event: bool

    # ------------------------------------------------------------------
    # StatusPatternRetrieveNode
    # ------------------------------------------------------------------

    # JSON array of the delivery-knowledge passages quoted in this answer.
    retrieved_chunks: Optional[str]

    # ------------------------------------------------------------------
    # ResponseSynthesizeNode
    # ------------------------------------------------------------------

    # The composed status explanation, before channel rendering.
    synthesized_response: Optional[str]

    # ------------------------------------------------------------------
    # EscalationCheckNode
    # ------------------------------------------------------------------

    # Whether this inquiry needs a human representative.
    escalation_required: bool

    # Why, as one of the node's declared codes; None when not escalating.
    escalation_reason_code: Optional[str]

    # The same reason in words, for the customer.
    escalation_reason: Optional[str]

    # ------------------------------------------------------------------
    # OutputFormatNode
    # ------------------------------------------------------------------

    # JSON array of the self-service options offered alongside the answer.
    delivery_options: Optional[str]

    # ------------------------------------------------------------------
    # PostProcessNode
    # ------------------------------------------------------------------

    # The released answer, or the withheld-output notice. Declared here because
    # the framework filters invoke()'s result to the keys this TypedDict names;
    # a key not listed is dropped without a word.
    formatted_output: Optional[str]
