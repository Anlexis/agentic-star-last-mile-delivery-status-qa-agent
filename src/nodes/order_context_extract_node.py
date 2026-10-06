"""AgentCore Platform v1.0"""

# RET-C2-577 — OrderContextExtractNode (inner domain node 2)
#
# Resolves the parcel the question is about into the delivery context the rest
# of the pipeline reasons over: a status code, a scan-event description, how
# many days late it is, and when it is now expected.
#
# Two sources, in order:
#
#   1. The caller's own records, when the request carried any. These come from
#      the order-management snapshot the deployment already holds; they arrive
#      through the validated contract, so every field here has been through a
#      closed set or a bounded parser before this node runs.
#   2. The agent's baseline, when the request carried none or none matched. The
#      answer is then built from delivery knowledge alone — a narrower answer,
#      correctly marked as one, rather than an invented status.
#
# The distinction is written into state (`order_context_source`) because the
# difference between "we looked this up" and "we have nothing to look up" is the
# difference between an answer and a guess, and the customer is entitled to know
# which one they got.
#
# No recipient name, address or contact detail is read here or written to state.
# The contract has no field for one, which is a stronger guarantee than a filter.

import json
from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.caller_contract import event_escalates, event_text, find_order

SOURCE_CALLER_RECORD = "caller_record"
SOURCE_BASELINE = "baseline"

# The answer when there is nothing to look the parcel up in. Deliberately says
# so rather than reporting a status: a delivery answer that invents a scan event
# is worse than one that admits it has no record.
_BASELINE_CONTEXT: Dict[str, Any] = {
    "order_status_code": "UNKNOWN",
    "order_last_event": "追跡情報が見つかりません / No tracking information",
    "order_delay_days": 0.0,
    "estimated_arrival": "お届け予定は確認できません / No delivery estimate available",
}


def _estimated_arrival(status_code: str, eta_hours: Optional[float]) -> str:
    """Render the arrival estimate for a resolved parcel.

    Whole hours below a day, whole days above, because an estimate quoted to the
    minute claims a precision the upstream scan data does not have.
    """
    if status_code == "DELIVERED":
        return "配達完了 / Delivery completed"
    if eta_hours is None:
        return "お届け予定は確認中です / Delivery estimate is being confirmed"
    hours = int(eta_hours)
    if hours <= 0:
        return "まもなくお届けの予定です / Arriving shortly"
    if hours < 24:
        return f"約{hours}時間後のお届け予定です / Expected in about {hours} hour(s)"
    days = hours // 24
    return f"約{days}日後のお届け予定です / Expected in about {days} day(s)"


class OrderContextExtractNode(FunctionNode):
    """Resolve the parcel into a delivery context, from caller records or the baseline."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        order_reference = state.get("order_reference")
        carrier_hint = state.get("carrier_hint")

        raw_contract = state.get("delivery_contract") or "{}"
        try:
            contract = json.loads(raw_contract)
        except (TypeError, ValueError):
            contract = {}
        if not isinstance(contract, dict):
            contract = {}

        record = find_order(contract, order_reference if isinstance(order_reference, str) else None)

        if record is None:
            context = dict(_BASELINE_CONTEXT)
            source = SOURCE_BASELINE
            resolved_reference = order_reference
            resolved_carrier = carrier_hint
        else:
            status_code = str(record["status_code"])
            delay_days = float(record.get("delay_days", 0.0))
            context = {
                "order_status_code": status_code,
                "order_last_event": event_text(record.get("last_event_code"))
                or "最新のスキャン情報はありません / No recent scan information",
                "order_delay_days": delay_days,
                "estimated_arrival": _estimated_arrival(status_code, record.get("eta_hours")),
            }
            source = SOURCE_CALLER_RECORD
            resolved_reference = record["order_reference"]
            resolved_carrier = record.get("carrier") or carrier_hint

        emit_trace_event(
            "order_context_extract",
            {
                "source": source,
                "reference_resolved": record is not None,
                "carrier_hint": resolved_carrier,
                "status_code": context["order_status_code"],
                "delay_days": context["order_delay_days"],
                "last_event_code": None if record is None else record.get("last_event_code"),
            },
            state,
        )

        return {
            **context,
            "order_context_source": source,
            "order_reference": resolved_reference,
            "carrier_hint": resolved_carrier,
            "escalating_event": (False if record is None else event_escalates(record.get("last_event_code"))),
            "status": AgentStatus.SUCCESS,
        }
