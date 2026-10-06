"""AgentCore Platform v1.0"""

# RET-C2-577 — OutputFormatNode (inner domain node 6)
#
# Renders the composed answer for the channel it is going out on, appends the
# escalation notice when one is due, and lists the self-service options that
# apply to the resolved status.
#
# Writes state["result"], which the outer graph node maps into the outer state
# and the output gate reads. Everything written here is either text this
# repository owns or a value already held to the caller contract's inert shape.

import json
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

CONTACT_SUPPORT_OPTION = "カスタマーサポートへ連絡 / Contact customer support"

OPTIONS_BY_STATUS: Dict[str, List[str]] = {
    "IN_TRANSIT": [
        "再配達依頼 / Reschedule delivery",
        "コンビニ受取に変更 / Change to convenience-store pickup",
        "配達日時変更 / Change delivery date or time",
    ],
    "DELAYED": [
        "再配達依頼 / Reschedule delivery",
        "コンビニ受取に変更 / Change to convenience-store pickup",
        CONTACT_SUPPORT_OPTION,
        "配送状況をメールで受け取る / Receive status updates by e-mail",
    ],
    "DELIVERED": [
        "不在票の確認 / Check the delivery notice",
        "宅配ボックスを確認 / Check the parcel locker",
        CONTACT_SUPPORT_OPTION,
    ],
    "UNKNOWN": [
        CONTACT_SUPPORT_OPTION,
        "追跡番号を再確認 / Re-check the tracking number",
    ],
}

_ESCALATION_NOTICE_LINE = (
    "\n\n■ このお問い合わせはカスタマーサポートへ転送されます。"
    " / This inquiry will be passed to our customer support team."
)

_ESCALATION_NOTICE_CHAT = (
    "\n\n-- Support follow-up --\n"
    "This inquiry has been flagged for a customer support representative, "
    "who will follow up with you."
)


class OutputFormatNode(FunctionNode):
    """Render the answer for the channel and attach the options and notice."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        synthesized = str(state.get("synthesized_response") or "").strip()
        escalate = bool(state.get("escalation_required"))
        reason = str(state.get("escalation_reason") or "")
        status_code = str(state.get("order_status_code") or "UNKNOWN")
        channel = str(state.get("output_channel") or "line").lower()

        response = synthesized
        if escalate:
            response += _ESCALATION_NOTICE_LINE if channel == "line" else _ESCALATION_NOTICE_CHAT
            if reason:
                response += f"\n理由 / Reason: {reason}"

        options: List[str] = list(OPTIONS_BY_STATUS.get(status_code, OPTIONS_BY_STATUS["UNKNOWN"]))
        if escalate and CONTACT_SUPPORT_OPTION not in options:
            options.append(CONTACT_SUPPORT_OPTION)

        emit_trace_event(
            "output_format",
            {
                "channel": channel,
                "escalate": escalate,
                "response_length": len(response),
                "options_count": len(options),
            },
            state,
        )

        return {
            "result": response,
            "delivery_options": json.dumps(options, ensure_ascii=False),
            "status": AgentStatus.SUCCESS,
        }
