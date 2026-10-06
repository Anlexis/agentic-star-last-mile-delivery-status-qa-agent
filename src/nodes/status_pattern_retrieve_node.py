"""AgentCore Platform v1.0"""

# RET-C2-577 — StatusPatternRetrieveNode (inner domain node 3)
#
# Selects the delivery-knowledge passages that explain the resolved status: what
# the status means, what the carrier's own delivery window is, and what the
# customer can do about it.
#
# The knowledge base ships as a literal here rather than as an index. That is a
# deliberate starting point, not an oversight: a template is adopted by replacing
# this table with a real retrieval call against the adopter's own carrier
# documentation, and shipping a small readable corpus makes the shape of what
# belongs there obvious. The passage cap comes from config/config.yaml.
#
# Every passage is text this repository owns. None of it is caller-supplied, so
# a passage cannot be a route by which a request's words reach the answer.

import json
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

KNOWLEDGE_BASE: Dict[str, List[str]] = {
    "IN_TRANSIT": [
        "配送中：お荷物は現在輸送中です。追跡番号でリアルタイムの位置情報をご確認いただけます。"
        " / In transit: your parcel is currently en route. "
        "You can check its position at any time with the tracking number.",
        "標準配達時間：ヤマト運輸は当日18:00まで、佐川急便は翌日12:00までの配達を目標としています。"
        " / Standard delivery windows: Yamato aims for 18:00 the same day; Sagawa for 12:00 the next.",
    ],
    "DELAYED": [
        "配送遅延ポリシー：天候・交通状況により配送が遅延する場合があります。"
        "遅延が2日以上の場合は再配達またはコンビニ受取をご利用ください。"
        " / Delay policy: weather and traffic can delay a delivery. "
        "After two days or more, reschedule or switch to convenience-store pickup.",
        "遅延補償：3日以上の遅延が確認された場合、カスタマーサービスへのエスカレーションが可能です。"
        " / Delay remedy: a confirmed delay of three days or more qualifies for a support escalation.",
    ],
    "DELIVERED": [
        "配達完了：お荷物は正常にお届けされました。不在の場合は不在票をご確認ください。"
        " / Delivered: the parcel was handed over successfully. "
        "If you were out, check for a delivery notice.",
        "配達済みの場合の手順：宅配ボックスまたは近隣への委託配達もご確認ください。"
        " / If it is marked delivered: check the parcel locker, and whether a neighbour accepted it.",
    ],
    "UNKNOWN": [
        "追跡情報が見つかりません：追跡番号を再確認の上、カスタマーサポートへお問い合わせください。"
        " / Tracking not found: check the tracking number and contact customer support.",
    ],
}

_DEFAULT_PASSAGES = KNOWLEDGE_BASE["UNKNOWN"]

_CARRIER_LABELS: Dict[str, str] = {
    "yamato": "ヤマト運輸",
    "sagawa": "佐川急便",
    "japan_post": "日本郵便",
    "custom": "自社配送",
}


class StatusPatternRetrieveNode(FunctionNode):
    """Retrieve the delivery-knowledge passages matching the resolved status."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        status_code = state.get("order_status_code") or "UNKNOWN"
        carrier_hint = state.get("carrier_hint")
        max_chunks = int(state.get("max_kb_chunks") or 4)

        passages: List[str] = list(KNOWLEDGE_BASE.get(str(status_code), _DEFAULT_PASSAGES))

        if carrier_hint:
            label = _CARRIER_LABELS.get(str(carrier_hint), str(carrier_hint))
            passages.insert(0, f"お問合せの配送業者: {label} / Carrier: {label}")

        passages = passages[: max(1, max_chunks)]

        emit_trace_event(
            "status_pattern_retrieve",
            {
                "status_code": status_code,
                "carrier_hint": carrier_hint,
                "passages_retrieved": len(passages),
                "passage_cap": max_chunks,
            },
            state,
        )

        return {
            "retrieved_chunks": json.dumps(passages, ensure_ascii=False),
            "status": AgentStatus.SUCCESS,
        }
