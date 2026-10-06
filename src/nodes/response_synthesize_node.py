"""AgentCore Platform v1.0"""

# RET-C2-577 — ResponseSynthesizeNode (inner domain node 4)
#
# Composes the customer-facing status explanation from the resolved delivery
# context and the retrieved knowledge passages.
#
# The composition is deterministic — a template, not a model call. That is why
# config/agent.yaml declares generation_mode "deterministic" and requires no
# model extra: the answer is assembled from a parcel's own status and passages
# this repository wrote, and nothing about that needs a language model.
#
# Only three kinds of value reach the text: the normalised parcel reference
# (inert by construction), numbers that have been through a bounded parser, and
# passages this repository owns. The customer's own words are never interpolated
# into the answer, so there is no newline a request could use to make a forged
# line look like a quoted delivery rule.

import json
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.nodes.order_context_extract_node import SOURCE_CALLER_RECORD

STATUS_LABELS: Dict[str, str] = {
    "IN_TRANSIT": "配送中 / In Transit",
    "DELAYED": "配送遅延中 / Delayed",
    "DELIVERED": "配達完了 / Delivered",
    "UNKNOWN": "不明 / Unknown",
}

_NO_REFERENCE = "（注文番号なし / no reference）"

# Said plainly when the answer rests on delivery knowledge alone. A customer who
# is told "we have no record of this parcel" can act; one who is handed a
# confident-sounding UNKNOWN cannot tell the two situations apart.
_BASELINE_NOTICE = (
    "※ この回答は一般的な配送ナレッジに基づくものです（該当のお荷物の記録は参照できませんでした）。"
    " / This answer is based on general delivery knowledge; no record for this parcel was available."
)


def _delay_line(delay_days: float) -> str:
    """One line stating how late the parcel is, or that it is not."""
    if delay_days <= 0:
        return "遅延日数 / Days late: 0"
    whole = int(delay_days)
    if float(whole) == delay_days:
        return f"遅延日数 / Days late: {whole}"
    return f"遅延日数 / Days late: {delay_days:g}"


class ResponseSynthesizeNode(FunctionNode):
    """Compose the status explanation from the delivery context and passages."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        status_code = str(state.get("order_status_code") or "UNKNOWN")
        last_event = str(state.get("order_last_event") or "")
        eta = str(state.get("estimated_arrival") or "")
        order_ref = state.get("order_reference") or _NO_REFERENCE
        delay_days = float(state.get("order_delay_days") or 0.0)
        source = state.get("order_context_source")

        raw_chunks = state.get("retrieved_chunks") or "[]"
        try:
            chunks = json.loads(raw_chunks)
        except (TypeError, ValueError):
            chunks = []
        passages: List[str] = [str(chunk) for chunk in chunks] if isinstance(chunks, list) else []

        status_label = STATUS_LABELS.get(status_code, status_code)
        reference_block = "\n".join(f"・{passage}" for passage in passages) or "（参考情報なし）"

        lines = [
            f"【配送状況 / Delivery Status】 {order_ref}",
            f"ステータス / Status: {status_label}",
            f"最終イベント / Last Event: {last_event}",
            _delay_line(delay_days),
            f"お届け予定 / ETA: {eta}",
        ]
        if source != SOURCE_CALLER_RECORD:
            lines.append(_BASELINE_NOTICE)
        lines.append("")
        lines.append("【参考情報 / Reference】")
        lines.append(reference_block)

        response = "\n".join(lines)

        emit_trace_event(
            "response_synthesize",
            {
                "status_code": status_code,
                "source": source,
                "passages_used": len(passages),
                "response_length": len(response),
            },
            state,
        )

        return {
            "synthesized_response": response,
            "status": AgentStatus.SUCCESS,
        }
