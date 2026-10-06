"""AgentCore Platform v1.0"""

# RET-C2-577 — PreProcessNode (outer backbone pre_process slot)
#
# The node that owns the caller contract. Everything a request carries — the
# question text and the structured delivery records — is admitted or refused
# here, before the delivery pipeline sees any of it.
#
# Why the screens live here rather than being left to the framework: the layer
# below is configurable and its coverage is not this template's to assume. An
# injection test that asserts "the platform refused it" passes where that gate
# is active and fails open where it is not, and a template that answers a
# forged instruction is the template's failure however it was configured. So
# the refusal is enforced in this node and proved by calling execute() directly,
# with no wrapper in front.

import json
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.caller_contract import (
    ContractRefusal,
    contains_disallowed_instruction,
    validate_input_context,
)

# Maximum characters for a delivery-status question.
MAX_QUERY_LENGTH = 2000

# Caller-visible refusal reasons. Closed set: the caller learns which rule the
# request broke, never what the request said.
REASON_EMPTY_REQUEST = "request_missing"
REASON_REQUEST_TOO_LONG = "request_too_long"
REASON_DISALLOWED_INSTRUCTION = "disallowed_instruction"

REFUSAL_REASONS = (REASON_EMPTY_REQUEST, REASON_REQUEST_TOO_LONG, REASON_DISALLOWED_INSTRUCTION)

# Prefix of the caller-visible refusal notice. Constant, so the whole notice is
# a constant plus a closed-set field path and reason code.
REFUSAL_NOTICE_PREFIX = "リクエストを受け付けられませんでした / Request not accepted — "


class PreProcessNode(FunctionNode):
    """Admit or refuse the request, and hand the validated contract downstream.

    Rejects empty and oversized questions, questions carrying a chat-template
    control token or a directive, and any structured parameter that falls
    outside the declared contract. On success it writes validated_input and the
    serialised contract; on refusal it writes an error status and a reason drawn
    from a closed set.
    """

    # External-facing trust gate — callers must be VERIFIED_EXTERNAL or higher.
    # src/api/server.py is what establishes that level for a standalone
    # deployment; without it every request arrives ANONYMOUS and is denied here.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        user_input = state.get("user_input", "")

        if not user_input or not isinstance(user_input, str) or not user_input.strip():
            return self._refuse("user_input", REASON_EMPTY_REQUEST, state)

        stripped = user_input.strip()

        if len(stripped) > MAX_QUERY_LENGTH:
            return self._refuse("user_input", REASON_REQUEST_TOO_LONG, state)

        if contains_disallowed_instruction(stripped):
            return self._refuse("user_input", REASON_DISALLOWED_INSTRUCTION, state)

        try:
            contract = validate_input_context(state.get("input_context") or {})
        except ContractRefusal as refusal:
            return self._refuse(refusal.field, refusal.reason, state)

        emit_trace_event(
            "pre_process_complete",
            {
                "input_chars": len(stripped),
                "orders_admitted": len(contract.get("orders") or []),
                "channel_supplied": "channel" in contract,
            },
            state,
        )

        return {
            "validated_input": stripped,
            # Serialised because State is a flat TypedDict and a nested mapping
            # is what msgpack checkpointing corrupts. Every value inside has
            # already passed the contract, so this string cannot carry caller
            # free text — which is also why the framework's credential scan over
            # this node's result can never fire on it.
            "delivery_contract": json.dumps(contract, ensure_ascii=False),
            "status": AgentStatus.SUCCESS,
        }

    # ------------------------------------------------------------------

    def _refuse(self, field: str, reason: str, state: AgentState) -> Dict[str, Any]:
        """Refuse the request, naming the field and the rule — never the value.

        The rejected value is already in the caller's hands; repeating it into a
        message, an error log or an audit record only puts it somewhere new.

        Both halves of what the caller sees are closed sets. `reason` is one of
        this module's or the contract's declared codes. `field` is a path built
        only from declared field names and integer indices — a caller key that
        is not one of ours is reported as unrecognised rather than echoed, so
        the refusal cannot become a channel for the caller's own text.

        The notice is set here rather than at the output boundary because the
        backbone routes a non-success status straight to finalize: post_process
        never runs on this path, so a refusal that did not render itself would
        reach the caller as a bare error with nothing to act on.
        """
        emit_trace_event("pre_process_refused", {"field": field, "reason": reason}, state)
        errors: List[str] = [f"{field}: {reason}"]
        return {
            "validated_input": "",
            "delivery_contract": "",
            "formatted_output": f"{REFUSAL_NOTICE_PREFIX}{field}: {reason}",
            "status": AgentStatus.ERROR,
            "error_log": errors,
        }
