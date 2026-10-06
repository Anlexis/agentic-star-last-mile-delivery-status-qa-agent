"""AgentCore Platform v1.0"""

# RET-C2-577 — PostProcessNode (outer backbone post_process slot)
#
# The output boundary. One invariant, stated here and enforced here:
#
#   A released delivery answer carries no credential-shaped string, no
#   recipient contact detail, and no full Japanese delivery address.
#
# There is no monetary precision grid in this template. It answers "where is my
# parcel", renders no amounts and no aggregates, so a rounding gate would have
# nothing to round; docs/02 says so rather than leaving a reader to wonder why
# the usual grid is absent. What replaces it is this invariant, enforced for
# every representation rather than the convenient ones.
#
# Three things worth knowing about how the enforcement is shaped.
#
# The credential half is the UNION of the platform's detector and a local set,
# never one or the other. Delegating entirely to the platform would be a
# narrowing disguised as an upgrade: its patterns describe credential FORMATS
# and match nothing of the `password=…` shape, which the local set does catch.
# Keeping only the local set is the other bypass: it has no AWS key id, no
# Stripe key, no connection string — and a value the platform catches and this
# node misses is worse than a plain miss, because the platform then raises from
# inside the node wrapper, which discards this node's entire delta, clearing
# included, and the un-gated answer survives in state.
#
# On a violation the node CLEARS the output-bearing fields rather than raising.
# The framework resolves the caller's output as `formatted_output or result`
# with no status check, so an output gate that merely raises still ships the
# un-gated answer inside the error envelope.
#
# The error the caller sees carries only values chosen from the constants in
# this module. state["error_log"] holds node-authored text and, at any node that
# ever interpolates a caught exception, upstream text as well; truncating or
# redacting that is not a closed-set contract, so none of it is published.

import re
from typing import Any, ClassVar, Dict, List, Optional, Pattern, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event

# ── Violation labels — a closed set ───────────────────────────────────────────

VIOLATION_CREDENTIAL = "credential"
VIOLATION_CONTACT_DETAIL = "contact_detail"
VIOLATION_POSTAL_ADDRESS = "postal_address"

VIOLATION_LABELS: Tuple[str, ...] = (
    VIOLATION_CREDENTIAL,
    VIOLATION_CONTACT_DETAIL,
    VIOLATION_POSTAL_ADDRESS,
)

# ── Caller-visible notices — constants, never assembled from state ────────────

REASON_OUTPUT_WITHHELD = "output_withheld"
REASON_WORKFLOW_FAILED = "workflow_failed"

NOTICES: Dict[str, str] = {
    REASON_OUTPUT_WITHHELD: (
        "回答は安全確認により保留されました。お手数ですがカスタマーサポートへお問い合わせください。"
        " / The answer was withheld by the output check. Please contact customer support."
    ),
    REASON_WORKFLOW_FAILED: (
        "リクエストを完了できませんでした。時間をおいて再度お試しください。"
        " / The request could not be completed. Please try again later."
    ),
}

# Every state key that can carry released text. Cleared together on a violation:
# clearing the answer and leaving the passage it was built from is not
# containment, it is a smaller leak.
OUTPUT_BEARING_FIELDS: Tuple[str, ...] = (
    "result",
    "synthesized_response",
    "delivery_options",
    "order_last_event",
    "estimated_arrival",
    "escalation_reason",
)

# ── Local credential patterns ─────────────────────────────────────────────────
#
# Kept because the platform detector does NOT carry them, not as a re-statement
# of what it already does. Both entries describe a credential being ASSIGNED
# rather than a credential's format, which is the axis the platform's pattern
# set does not cover at all.
_LOCAL_CREDENTIAL_PATTERNS: List[Tuple[Pattern[str], str]] = [
    (
        re.compile(r"(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[=:]\s*\S+", re.IGNORECASE),
        "credential_assignment",
    ),
    (re.compile(r"(?:^|[^A-Za-z0-9])(?:pk|ak)-[A-Za-z0-9]{20,}"), "prefixed_key"),
]

# ── Contact detail ────────────────────────────────────────────────────────────
#
# A recipient's phone number or e-mail address in a delivery answer is a
# disclosure whatever else is in the sentence.
#
# The digit-run guards are lookarounds on non-digits rather than \b. Python's \b
# is computed over \w, which includes kana and kanji — so `090-1234-5678` inside
# Japanese text, written without spaces as Japanese is, has no word boundary at
# either end and a \b-anchored pattern silently does not match. The failing case
# is the ordinary one.
_CONTACT_PATTERNS: List[Pattern[str]] = [
    # Japanese mobile and landline, hyphenated or run together.
    re.compile(r"(?<![0-9])0[789]0[-‐−ー－ ]?[0-9]{4}[-‐−ー－ ]?[0-9]{4}(?![0-9])"),
    re.compile(r"(?<![0-9])0[1-9][0-9]{0,3}[-‐−ー－][0-9]{1,4}[-‐−ー－][0-9]{4}(?![0-9])"),
    # E-mail address.
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
]

# ── Postal address ────────────────────────────────────────────────────────────
#
# The shipped pattern was `\d{3}-\d{4}\s*[都道府県]`, which required the
# prefecture SUFFIX character to sit immediately after the postal code. Japanese
# addresses are written the other way round — `123-4567 東京都渋谷区…` puts 東 there
# and 都 two characters later — so the pattern matched the form nobody writes and
# missed the form everybody writes. It was fail-open on its own headline case.
#
# Two forms are recognised instead, and a prefecture name ALONE is deliberately
# not one of them: "東京都内は当日配達です" is an ordinary sentence about a delivery
# window, and a gate that refuses it refuses real work.

_PREFECTURES = (
    "北海道|青森県|岩手県|宮城県|秋田県|山形県|福島県|茨城県|栃木県|群馬県|埼玉県|千葉県|東京都|神奈川県|"
    "新潟県|富山県|石川県|福井県|山梨県|長野県|岐阜県|静岡県|愛知県|三重県|滋賀県|京都府|大阪府|兵庫県|"
    "奈良県|和歌山県|鳥取県|島根県|岡山県|広島県|山口県|徳島県|香川県|愛媛県|高知県|福岡県|佐賀県|長崎県|"
    "熊本県|大分県|宮崎県|鹿児島県|沖縄県"
)

_ADDRESS_PATTERNS: List[Pattern[str]] = [
    # A postal code with a prefecture close behind it, in any of the ways the
    # code itself gets written: with or without 〒, with or without the hyphen,
    # in halfwidth or fullwidth digits.
    re.compile(r"[〒]?[0-9０-９]{3}[-‐−ー－]?[0-9０-９]{4}[\s　]{0,4}(?:" + _PREFECTURES + r")"),
    # A prefecture followed by a municipality and a street block — the shape a
    # complete address has even when the postal code is omitted.
    re.compile(
        r"(?:" + _PREFECTURES + r")[^\n]{0,20}?[市区町村郡][^\n]{0,20}?" r"[0-9０-９]{1,4}[-‐−ー－丁目][0-9０-９]{1,4}"
    ),
]


def _security_gate_output(content: str) -> Optional[str]:
    """The domain output check. Returns a violation label, or None when clean.

    A module-level function so the check has one call site the whole repository
    can point at, and so it can be driven directly in a test without a graph.

    Order matters and is not arbitrary: the pattern scans run over the content
    exactly as it will be released, with nothing rewritten beforehand. A
    transformation applied first can destroy the very shape a later scan is
    looking for.
    """
    if not content:
        return None

    # Platform detector first: it is the floor, and a value it catches that this
    # node lets through makes the platform raise inside the node wrapper, which
    # discards the clearing below along with everything else this node returns.
    if detect_credentials(content):
        return VIOLATION_CREDENTIAL
    for pattern, _name in _LOCAL_CREDENTIAL_PATTERNS:
        if pattern.search(content):
            return VIOLATION_CREDENTIAL

    for pattern in _CONTACT_PATTERNS:
        if pattern.search(content):
            return VIOLATION_CONTACT_DETAIL

    for pattern in _ADDRESS_PATTERNS:
        if pattern.search(content):
            return VIOLATION_POSTAL_ADDRESS

    return None


class PostProcessNode(FunctionNode):
    """Release the answer, or withhold it and say so in closed-set terms."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        if state.get("status") == AgentStatus.ERROR.value:
            # Defensive: the backbone routes a non-success status straight to
            # finalize, so this branch is not reached in the shipped topology.
            # It is kept because "not reachable today" and "cannot leak" are
            # different claims, and only the second one is worth relying on.
            emit_trace_event("post_process_upstream_error", {"reason": REASON_WORKFLOW_FAILED}, state)
            return self._withhold(REASON_WORKFLOW_FAILED)

        result = state.get("result")
        content = "" if result is None else str(result)

        violation = _security_gate_output(content)
        if violation is not None:
            emit_trace_event("post_process_output_blocked", {"violation": violation}, state)
            return self._withhold(REASON_OUTPUT_WITHHELD)

        emit_trace_event("post_process_complete", {"output_chars": len(content)}, state)

        return {
            "formatted_output": content,
            "status": AgentStatus.SUCCESS,
        }

    # ------------------------------------------------------------------

    def _withhold(self, reason: str) -> Dict[str, Any]:
        """Return an ERROR result carrying no released text.

        Every output-bearing field is present and empty rather than absent:
        LangGraph merges a node's partial delta into state, so a field left out
        keeps whatever it held, and the framework's envelope reads
        `formatted_output or result` with no status check.

        The notice is a constant and therefore truthy. A falsy replacement would
        re-open that same fallback onto whatever survived.
        """
        contained: Dict[str, Any] = {field: "" for field in OUTPUT_BEARING_FIELDS}
        contained["formatted_output"] = NOTICES[reason]
        contained["status"] = AgentStatus.ERROR
        return contained
