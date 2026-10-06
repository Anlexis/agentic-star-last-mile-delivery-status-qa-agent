"""AgentCore Platform v1.0"""

# RET-C2-577 — EscalationCheckNode (inner domain node 5)
#
# Decides whether the customer should be handed to a human representative.
#
# Three rules, in the order a support desk would apply them:
#
#   1. The parcel cannot be located at all. Nothing in the delivery knowledge
#      resolves that; a person has to look.
#   2. The last scan event is one that always warrants a person — a suspected
#      loss, a reported damage. The event codes carry that property themselves,
#      set where the code is defined rather than re-derived here from words in a
#      description, which is how a translation change silently disables a rule.
#   3. The parcel is later than the deployment's threshold. The threshold comes
#      from config/config.yaml and reaches this node through seeded state, so
#      changing the file changes the answer.
#
# The decision is advisory: the customer still receives the status explanation.
# What escalation adds is a notice and a route to a person.
#
# The comparison is the reason every number in this template goes through a
# finite parser. `float("nan") >= 3.0` is False, as is every other comparison
# against NaN — so a NaN delay would report itself as inside the threshold and
# this node would decline to escalate the one case it exists for, silently.

from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

DEFAULT_THRESHOLD_DAYS = 3.0

REASON_UNRESOLVED = "unresolved_parcel"
REASON_EVENT = "escalating_scan_event"
REASON_DELAY = "delay_over_threshold"

_REASON_TEXT = {
    REASON_UNRESOLVED: (
        "追跡情報が見つからないため、カスタマーサポートへのエスカレーションが必要です。"
        " / No tracking information is available; a support representative needs to look into this."
    ),
    REASON_EVENT: (
        "紛失または破損の可能性が報告されているため、カスタマーサポートへご連絡ください。"
        " / A loss or damage has been reported; please contact customer support."
    ),
}


class EscalationCheckNode(FunctionNode):
    """Decide whether this inquiry needs a human representative."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        status_code = str(state.get("order_status_code") or "UNKNOWN")
        delay_days = float(state.get("order_delay_days") or 0.0)
        threshold = float(state.get("escalation_threshold_days") or DEFAULT_THRESHOLD_DAYS)
        escalating_event = bool(state.get("escalating_event"))

        reason_code: Optional[str] = None
        if status_code == "UNKNOWN":
            reason_code = REASON_UNRESOLVED
        elif escalating_event:
            reason_code = REASON_EVENT
        elif delay_days >= threshold:
            reason_code = REASON_DELAY

        escalation_required = reason_code is not None

        if reason_code == REASON_DELAY:
            reason_text: Optional[str] = (
                f"配送遅延が{delay_days:g}日となり、基準の{threshold:g}日に達しています。"
                "カスタマーサポートへのエスカレーションを推奨します。"
                f" / The parcel is {delay_days:g} day(s) late, at or beyond the {threshold:g}-day "
                "threshold; a support escalation is recommended."
            )
        else:
            reason_text = _REASON_TEXT.get(reason_code) if reason_code else None

        emit_trace_event(
            "escalation_check",
            {
                "status_code": status_code,
                "delay_days": delay_days,
                "threshold_days": threshold,
                "escalation_required": escalation_required,
                "reason_code": reason_code,
            },
            state,
        )

        return {
            "escalation_required": escalation_required,
            "escalation_reason_code": reason_code,
            "escalation_reason": reason_text,
            "status": AgentStatus.SUCCESS,
        }
