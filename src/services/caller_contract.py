"""AgentCore Platform v1.0"""

# The caller-data contract for the delivery-status pipeline.
#
# Everything a caller may send alongside the question arrives here first and
# leaves as either a validated contract or a refusal that names one field and a
# reason drawn from a closed set. Nothing in between reaches a node.
#
# Three properties the rest of the agent depends on:
#
#   1. Every value that renders into the customer-facing answer is either a
#      caller identifier locked to an inert alphabet, or text this repository
#      owns. A carrier's own event wording never travels; the caller sends an
#      event CODE and the description is looked up here. So there is no path by
#      which caller free text becomes part of a delivery answer.
#   2. Every number is finite and in range. A delay of NaN days parses through
#      float() and then compares False against every threshold, which would
#      silently suppress the escalation this agent exists to raise. Non-finite
#      values are refused, not clamped.
#   3. Unknown keys are dropped rather than ignored. Ignoring leaves them in
#      state["input_context"], which the backbone's first node copies verbatim
#      into its own result, where the mandatory output gate scans it. One
#      credential-shaped string in an undeclared key ends the run at node one
#      with a traceback and no explanation.

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

# ── Closed sets ───────────────────────────────────────────────────────────────

# Carriers this agent holds delivery knowledge for.
CARRIERS: Tuple[str, ...] = ("yamato", "sagawa", "japan_post", "custom")

# Delivery status codes the pipeline reasons about.
STATUS_CODES: Tuple[str, ...] = ("IN_TRANSIT", "DELAYED", "DELIVERED", "UNKNOWN")

# Output channels the formatter renders for.
CHANNELS: Tuple[str, ...] = ("line", "chat", "email")

# Scan-event codes a caller may report, each mapped to the description this
# repository publishes for it. The caller chooses a code; the words are ours.
# `escalates` marks the codes that always warrant a human hand-off regardless of
# how long the parcel has been delayed.
EVENT_CATALOGUE: Dict[str, Dict[str, Any]] = {
    "sorting_centre": {
        "text": "集荷センター通過 / Passed sorting centre",
        "escalates": False,
    },
    "out_for_delivery": {
        "text": "配達中 / Out for delivery",
        "escalates": False,
    },
    "weather_delay": {
        "text": "悪天候により配送が遅れています / Delay: adverse weather",
        "escalates": False,
    },
    "traffic_delay": {
        "text": "交通事情により配送が遅れています / Delay: traffic conditions",
        "escalates": False,
    },
    "address_issue": {
        "text": "住所が確認できないため保留中です / Held: address could not be resolved",
        "escalates": False,
    },
    "recipient_absent": {
        "text": "ご不在のため持ち戻りました / Returned: recipient absent",
        "escalates": False,
    },
    "delivered": {
        "text": "お届け済み / Delivered",
        "escalates": False,
    },
    "locker_delivered": {
        "text": "宅配ボックスへお届け済み / Delivered to parcel locker",
        "escalates": False,
    },
    "lost_in_transit": {
        "text": "輸送中の紛失の疑いがあります / Suspected loss in transit",
        "escalates": True,
    },
    "damaged": {
        "text": "破損の申告を受け付けています / Damage reported",
        "escalates": True,
    },
    "unknown": {
        "text": "追跡情報が見つかりません / No tracking information",
        "escalates": True,
    },
}

EVENT_CODES: Tuple[str, ...] = tuple(EVENT_CATALOGUE)

# ── Structural bounds ─────────────────────────────────────────────────────────

MAX_ORDERS = 20
MAX_DELAY_DAYS = 365.0
MAX_ETA_HOURS = 8760.0  # one year; beyond this an "estimate" is not one

# Caller identifiers that render into the answer. Inert by construction: no
# spaces, no punctuation a reader could mistake for formatting, no case to carry
# a hidden word boundary. A reference outside this shape is refused rather than
# escaped, because escaping is a decision about presentation and this is a
# decision about admission.
INERT_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]{1,32}$")

# ── Refusal reasons — a closed set ────────────────────────────────────────────
#
# A refusal names a field and one of these. It never repeats the value: the
# caller already has it, and a rejected value echoed into a message or an audit
# record is the same value in one more place.

REASON_NOT_AN_OBJECT = "not_an_object"
REASON_NOT_A_LIST = "not_a_list"
REASON_TOO_MANY_ENTRIES = "too_many_entries"
REASON_UNKNOWN_FIELD = "unknown_field"
REASON_NOT_IN_CLOSED_SET = "not_in_closed_set"
REASON_NOT_INERT_IDENTIFIER = "not_an_inert_identifier"
REASON_NOT_A_FINITE_NUMBER = "not_a_finite_number"
REASON_OUT_OF_RANGE = "out_of_range"
REASON_DISALLOWED_INSTRUCTION = "disallowed_instruction"
REASON_MISSING_FIELD = "missing_required_field"

REFUSAL_REASONS: Tuple[str, ...] = (
    REASON_NOT_AN_OBJECT,
    REASON_NOT_A_LIST,
    REASON_TOO_MANY_ENTRIES,
    REASON_UNKNOWN_FIELD,
    REASON_NOT_IN_CLOSED_SET,
    REASON_NOT_INERT_IDENTIFIER,
    REASON_NOT_A_FINITE_NUMBER,
    REASON_OUT_OF_RANGE,
    REASON_DISALLOWED_INSTRUCTION,
    REASON_MISSING_FIELD,
)


# Every name a field path may contain. Anything else becomes a placeholder.
_ORDER_FIELD_NAMES = frozenset(
    {"order_reference", "carrier", "status_code", "last_event_code", "delay_days", "eta_hours"}
)
_CONTEXT_FIELD_NAMES = frozenset({"orders", "channel"})
_DECLARED_NAMES = _ORDER_FIELD_NAMES | _CONTEXT_FIELD_NAMES | {"input_context"}


class ContractRefusal(Exception):
    """A caller field failed admission. Carries the field and a closed-set reason."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


# ── Disallowed-instruction screen ─────────────────────────────────────────────
#
# Two families, screened as classes rather than as a list of phrases seen once.
#
# Chat-template control tokens are the family a phrase-based screen misses
# entirely. `<|im_start|>system …` carries no imperative English at all — the
# directive lives in the frame, not the words. The platform's own detector
# scores `<<SYS>>` as no finding, which is exactly what makes it worth screening
# here rather than relying on the layer below.
#
# Directive phrases are anchored on both ends and require an object. An
# unanchored `act as a` matches "Transact as a settlement agent"; an unanchored
# SQL verb matches "Insert Into Trust Holdings". A screen that refuses ordinary
# sentences is not a stricter screen, it is a broken product.

_CONTROL_TOKEN_RE = re.compile(
    r"""(
        <\|[^|>]{0,64}\|>          # <|im_start|>, <|im_end|>, <|endoftext|>
      | \[/?INST\]                 # [INST] … [/INST]
      | <</?SYS>>                  # <<SYS>> … <</SYS>>
      | </?s>                      # <s> … </s>
      | </?system>                 # <system> … </system>
      | </?\|?assistant\|?>        # <assistant>, <|assistant|>
    )""",
    re.IGNORECASE | re.VERBOSE,
)

_DIRECTIVE_RE = re.compile(
    r"""(
        \bignore\s+(?:all\s+|any\s+)?(?:the\s+)?
            (?:previous|prior|above|preceding|earlier|foregoing)\s+
            (?:instructions?|rules?|prompts?|directions?|messages?)\b
      | \bdisregard\s+(?:all\s+|any\s+)?(?:the\s+)?
            (?:previous|prior|above|preceding|earlier|system)\s+
            (?:instructions?|rules?|prompts?|directions?|messages?)\b
      | \byou\s+are\s+now\s+(?:a|an|the)\s
      | \bact\s+as\s+(?:a|an|the)\s+(?:system|assistant|admin|administrator|developer|root)\b
      | \bnew\s+(?:system\s+)?(?:instructions?|prompt)\s*[:：]
      | ^\s*system\s*[:：]
      | \breveal\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions?)\b
    )""",
    re.IGNORECASE | re.VERBOSE | re.MULTILINE,
)

# Characters that carry no glyph and exist mainly to split a word a screen is
# looking for. Removed before the second pass, never before the first: a payload
# that only becomes a directive once they are gone is still a directive.
# Ranges, not literals: a character with no glyph is one nobody can review in a diff.
#   200B-200F zero-width space/non-joiner/joiner and the LTR/RTL marks
#   202A-202E bidirectional embedding and override
#   2060-2064 word joiner and the invisible operators
#   FEFF byte-order mark, 00AD soft hyphen
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad]")

_MARKUP_RE = re.compile(r"<[^<>]{0,120}>")


def _screen_variants(text: str) -> List[str]:
    """The forms a single string is screened in.

    Raw first, because a strip can destroy the very token that identifies the
    attack: removing `<|im_start|>` and forwarding the rest converts a
    recognisable control-token payload into ordinary-looking prose. Then the
    stripped forms, because a directive can be re-assembled out of fragments the
    raw pass reads as markup (`ig<b>nore all previous instructions`).

    A glyph-less character is removed in BOTH directions, because it can be
    standing in for either nothing or a space and only the reader knows which.
    Deleting it re-assembles `pass<ZWSP>word`; replacing it with a space
    re-assembles `Ignore<ZWSP>previous<ZWSP>instructions`, which deletion turns
    into one unreadable token that a whitespace-separated pattern then misses.
    Screening only one way left exactly that hole, found by probing it.
    """
    normalised = unicodedata.normalize("NFKC", text)
    variants = [text, normalised]
    for filler in ("", " "):
        rejoined = _INVISIBLE_RE.sub(filler, normalised)
        variants.append(rejoined)
        variants.append(_MARKUP_RE.sub(filler, rejoined))
    return variants


def contains_disallowed_instruction(text: str) -> bool:
    """True when any screened form of *text* carries a control token or a directive."""
    for variant in _screen_variants(text):
        if _CONTROL_TOKEN_RE.search(variant) or _DIRECTIVE_RE.search(variant):
            return True
    return False


def screen_structure(value: object, field: str) -> None:
    """Walk a parsed payload depth-first and refuse the first disallowed string.

    KEYS are screened as well as values. A payload can put its directive in a
    field name, and a scan that only reads values would forward it untouched.
    The walk runs after parsing, so a `\\u`-escaped payload is screened in its
    decoded form — the escape is a transport detail, not a disguise.
    """
    if isinstance(value, str):
        if contains_disallowed_instruction(value):
            raise ContractRefusal(field, REASON_DISALLOWED_INSTRUCTION)
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = key if isinstance(key, str) else str(key)
            if contains_disallowed_instruction(key_text):
                raise ContractRefusal(field, REASON_DISALLOWED_INSTRUCTION)
            screen_structure(nested, f"{field}.{_safe_key(key_text)}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            screen_structure(item, f"{field}[{index}]")


UNRECOGNISED_FIELD = "<unrecognised>"

# Placeholder used for any path segment that is not one of this contract's own
# declared names. A field path reaches the caller inside a refusal message, so
# every segment of it has to be a value this repository chose. Echoing the
# caller's key back instead looks helpful and is not: a key name is caller data,
# it can be arbitrarily long, and it can itself be credential-shaped — in which
# case the platform's output scan fires on the refusal that was trying to
# explain the problem.
_ANONYMOUS_FIELD = "<field>"


def _safe_key(key: str) -> str:
    """Render a path segment, or replace it when it is not a declared name."""
    return key if key in _DECLARED_NAMES else _ANONYMOUS_FIELD


# ── Primitive validators ──────────────────────────────────────────────────────


def finite_in_range(value: object, *, field: str, low: float, high: float) -> float:
    """Parse a caller number, or refuse.

    Rejects, in this order: booleans (``isinstance(True, int)`` is True in
    Python, so ``delay_days: true`` would otherwise arrive as one day); values
    that are neither a number nor a numeric string; NaN and ±Infinity, which
    parse through ``float()`` without complaint and then compare False against
    every bound, so a NaN delay would report itself as under every threshold;
    and finally values outside the declared range.
    """
    if isinstance(value, bool):
        raise ContractRefusal(field, REASON_NOT_A_FINITE_NUMBER)
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError):
            raise ContractRefusal(field, REASON_NOT_A_FINITE_NUMBER) from None
    else:
        raise ContractRefusal(field, REASON_NOT_A_FINITE_NUMBER)
    if not math.isfinite(number):
        raise ContractRefusal(field, REASON_NOT_A_FINITE_NUMBER)
    if number < low or number > high:
        raise ContractRefusal(field, REASON_OUT_OF_RANGE)
    return number


def in_closed_set(value: object, *, field: str, allowed: Tuple[str, ...]) -> str:
    """Return *value* when it is one of *allowed*, else refuse."""
    if not isinstance(value, str) or value not in allowed:
        raise ContractRefusal(field, REASON_NOT_IN_CLOSED_SET)
    return value


def inert_identifier(value: object, *, field: str) -> str:
    """Return *value* when it is an inert identifier, else refuse."""
    if not isinstance(value, str) or not INERT_IDENTIFIER_RE.match(value):
        raise ContractRefusal(field, REASON_NOT_INERT_IDENTIFIER)
    return value


# ── The contract ──────────────────────────────────────────────────────────────

_ORDER_FIELDS = _ORDER_FIELD_NAMES
_CONTEXT_FIELDS = _CONTEXT_FIELD_NAMES

# Keys the platform itself puts into input_context, not the caller. The
# Marketplace runner invokes every agent as
#     agent.invoke(message, ctx=ctx, input_context={"conversation_history": history})
# (agenticstar-agentcore 1.0.1, shared/bootstrap/marketplace_app.py), whatever
# the user typed. Refusing it as an unknown field refused every chat request
# before the question was read. They are discarded, not validated: nothing in
# this pipeline reads prior turns, so there is nothing to admit, and screening a
# transcript would let one earlier message refuse every later one. Discarding
# adds no exposure — the backbone's first node has already copied the raw
# input_context into state before this contract runs.
PLATFORM_RESERVED_KEYS = frozenset({"conversation_history"})


def _validate_order(raw: object, field: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContractRefusal(field, REASON_NOT_AN_OBJECT)

    unknown = [key for key in raw if key not in _ORDER_FIELDS]
    if unknown:
        raise ContractRefusal(f"{field}.{UNRECOGNISED_FIELD}", REASON_UNKNOWN_FIELD)

    for required in ("order_reference", "status_code"):
        if required not in raw:
            raise ContractRefusal(f"{field}.{required}", REASON_MISSING_FIELD)

    order: Dict[str, Any] = {
        "order_reference": inert_identifier(raw["order_reference"], field=f"{field}.order_reference"),
        "status_code": in_closed_set(raw["status_code"], field=f"{field}.status_code", allowed=STATUS_CODES),
    }
    if "carrier" in raw:
        order["carrier"] = in_closed_set(raw["carrier"], field=f"{field}.carrier", allowed=CARRIERS)
    if "last_event_code" in raw:
        order["last_event_code"] = in_closed_set(
            raw["last_event_code"], field=f"{field}.last_event_code", allowed=EVENT_CODES
        )
    order["delay_days"] = finite_in_range(
        raw.get("delay_days", 0), field=f"{field}.delay_days", low=0.0, high=MAX_DELAY_DAYS
    )
    if "eta_hours" in raw:
        order["eta_hours"] = finite_in_range(raw["eta_hours"], field=f"{field}.eta_hours", low=0.0, high=MAX_ETA_HOURS)
    return order


def validate_input_context(input_context: object) -> Dict[str, Any]:
    """Turn a caller's structured parameters into a validated contract, or refuse.

    Returns a contract carrying only declared, bounded fields. An absent or
    empty payload returns an empty contract: the pipeline then answers from its
    own delivery knowledge alone, which is a narrower answer but a correct one.
    """
    if input_context is None:
        return {}
    if not isinstance(input_context, dict):
        raise ContractRefusal("input_context", REASON_NOT_AN_OBJECT)
    input_context = {k: v for k, v in input_context.items() if k not in PLATFORM_RESERVED_KEYS}
    if not input_context:
        return {}

    # Screen before shape-checking: a directive in an undeclared field is still
    # a directive, and reporting "unknown_field" for it would be the less useful
    # of the two true statements.
    screen_structure(input_context, "input_context")

    unknown = [key for key in input_context if key not in _CONTEXT_FIELDS]
    if unknown:
        raise ContractRefusal(f"input_context.{UNRECOGNISED_FIELD}", REASON_UNKNOWN_FIELD)

    contract: Dict[str, Any] = {}

    if "channel" in input_context:
        contract["channel"] = in_closed_set(input_context["channel"], field="input_context.channel", allowed=CHANNELS)

    if "orders" in input_context:
        raw_orders = input_context["orders"]
        if not isinstance(raw_orders, list):
            raise ContractRefusal("input_context.orders", REASON_NOT_A_LIST)
        if len(raw_orders) > MAX_ORDERS:
            raise ContractRefusal("input_context.orders", REASON_TOO_MANY_ENTRIES)
        contract["orders"] = [
            _validate_order(raw, f"input_context.orders[{index}]") for index, raw in enumerate(raw_orders)
        ]

    return contract


def find_order(contract: Dict[str, Any], reference: Optional[str]) -> Optional[Dict[str, Any]]:
    """The caller record whose reference matches *reference*, if any.

    Matching normalises the reference the same way the contract does — lower
    case, and the separators a customer types (`-`, space, `#`) folded to the
    underscore the inert alphabet uses. A customer writing `TRK-8291847` and a
    system record keyed `trk_8291847` are the same parcel.
    """
    orders: List[Dict[str, Any]] = contract.get("orders") or []
    if not orders:
        return None
    if reference is None:
        return orders[0] if len(orders) == 1 else None
    wanted = normalise_reference(reference)
    for order in orders:
        if order["order_reference"] == wanted:
            return order
    return None


_SEPARATOR_RE = re.compile(r"[\s\-#/]+")


def normalise_reference(reference: str) -> str:
    """Fold a human-typed reference onto the inert alphabet used by the contract."""
    folded = _SEPARATOR_RE.sub("_", unicodedata.normalize("NFKC", reference).strip().lower())
    return re.sub(r"[^a-z0-9_]", "", folded)[:32]


def event_text(code: Optional[str]) -> Optional[str]:
    """The description this repository publishes for a scan-event code."""
    if code is None:
        return None
    entry = EVENT_CATALOGUE.get(code)
    return None if entry is None else str(entry["text"])


def event_escalates(code: Optional[str]) -> bool:
    """Whether a scan-event code always warrants a human hand-off."""
    if code is None:
        return False
    entry = EVENT_CATALOGUE.get(code)
    return bool(entry and entry["escalates"])
