"""AgentCore Platform v1.0"""

# RET-C2-577 — QueryNormalizeNode (inner domain node 1)
#
# Reads the customer's question and pulls out the two things the rest of the
# pipeline needs from it: the parcel the question is about, and the carrier it
# was sent with.
#
# The extracted reference is normalised onto the same inert alphabet the caller
# contract uses — lower case, separators folded to underscore. That serves two
# purposes at once: it is the key the delivery records are matched on, and it is
# the only form that is ever echoed back to the customer. What the answer shows
# is exactly what the lookup used, and it cannot carry a character that would
# read as formatting.

import json
import re
from typing import Any, ClassVar, Dict, List, Optional, Pattern

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.caller_contract import normalise_reference

# Tracking-reference shapes seen on Japanese last-mile parcels.
_TRACKING_PATTERNS: List[Pattern[str]] = [
    re.compile(r"TRK[-_ ]?[0-9]{5,12}", re.IGNORECASE),  # generic TRK-XXXXXX
    re.compile(r"[A-Z]{2}[0-9]{9}JP", re.IGNORECASE),  # international EMS
    re.compile(r"追跡番号[:\s：]*([0-9A-Za-z\-]{6,})"),  # "追跡番号" prefix
    re.compile(r"#([0-9]{10,12})"),  # bare numeric, chat style
    re.compile(r"(?<![0-9])([0-9]{12})(?![0-9])"),  # 12-digit
    re.compile(r"(?<![0-9])([0-9]{11})(?![0-9])"),  # 11-digit
]

# Carrier name keywords, in the language customers actually write them in.
_CARRIER_KEYWORDS: Dict[str, List[str]] = {
    "yamato": ["ヤマト", "yamato", "クロネコ", "kuroneko", "宅急便"],
    "sagawa": ["佐川", "sagawa"],
    "japan_post": ["日本郵便", "japan_post", "japanpost", "ゆうパック", "ゆうメール", "郵便局"],
    "custom": ["自社", "自社配送"],
}


def extract_order_reference(text: str) -> Optional[str]:
    """The parcel reference in *text*, normalised, or None."""
    for pattern in _TRACKING_PATTERNS:
        match = pattern.search(text)
        if match:
            raw = match.group(1) if match.lastindex else match.group(0)
            normalised = normalise_reference(raw)
            if normalised:
                return normalised
    return None


def detect_carrier(text: str, scope: List[str]) -> Optional[str]:
    """The carrier mentioned in *text*, when it is one this deployment serves.

    `scope` comes from config/config.yaml. A carrier outside it is not treated as
    a detection: the question is still answered, just without the passage that
    describes that carrier's own delivery windows, which this deployment has no
    knowledge of.
    """
    lowered = text.lower()
    for carrier, keywords in _CARRIER_KEYWORDS.items():
        if scope and carrier not in scope:
            continue
        for keyword in keywords:
            if keyword.lower() in lowered:
                return carrier
    return None


def normalize_text(text: str) -> str:
    """Lower case, whitespace collapsed, trimmed."""
    return re.sub(r"\s+", " ", text.strip().lower())


class QueryNormalizeNode(FunctionNode):
    """Normalise the question; extract the parcel reference and carrier hint.

    required_trust_level is ANONYMOUS because this is an inner node: the caller's
    level was already checked at the backbone's external boundary, and the
    invocation context crosses unchanged. Declaring VERIFIED_EXTERNAL here as
    well would deny nothing extra and would break the pipeline the moment the
    agent were composed inside another one.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        raw = state.get("user_input", "") or ""
        text = raw if isinstance(raw, str) else str(raw)

        scope_json = state.get("carrier_scope_config") or "[]"
        try:
            scope = json.loads(scope_json)
        except (TypeError, ValueError):
            scope = []
        if not isinstance(scope, list):
            scope = []

        normalised = normalize_text(text)
        order_ref = extract_order_reference(text)
        carrier_hint = detect_carrier(text, [str(entry) for entry in scope])

        emit_trace_event(
            "query_normalize",
            {
                "normalized_length": len(normalised),
                "order_reference_found": order_ref is not None,
                "carrier_hint": carrier_hint,
                "carriers_in_scope": len(scope),
            },
            state,
        )

        result: Dict[str, Any] = {
            "normalized_query": normalised,
            "order_reference": order_ref,
            "carrier_hint": carrier_hint,
            "status": AgentStatus.SUCCESS,
        }
        return result
