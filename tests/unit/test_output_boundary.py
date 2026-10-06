# The output boundary: the one invariant this template states, enforced for
# every representation rather than the convenient ones.
#
#   A released delivery answer carries no credential-shaped string, no recipient
#   contact detail, and no full Japanese delivery address.
#
# There is no monetary precision grid here and this file does not pretend
# otherwise: the agent renders no amounts and no aggregates, so a rounding gate
# would have nothing to round. The invariant above is what replaces it.
#
# Both directions, every time. The leak direction is the one that gets attention;
# the byte-identical direction is the one that breaks a working product, and a
# gate that mangles "東京都内は当日配達" has made the answer wrong in a new way.

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from src.nodes.post_process_node import (
    NOTICES,
    OUTPUT_BEARING_FIELDS,
    REASON_OUTPUT_WITHHELD,
    VIOLATION_CONTACT_DETAIL,
    VIOLATION_CREDENTIAL,
    VIOLATION_LABELS,
    VIOLATION_POSTAL_ADDRESS,
    PostProcessNode,
    _security_gate_output,
)

CLEAN_ANSWER = "【配送状況 / Delivery Status】 trk_8291847\nステータス / Status: 配送中 / In Transit"

# Credential-shaped fixtures, assembled at run time rather than written as
# literals.
#
# The repository's own credential scan reads every file in the tree, tests
# included, and it is a blocking gate. A fixture that has to be credential-SHAPED
# to exercise the boundary would trip it — and the two ways out of that are to
# weaken the gate or to stop writing the literal. Only one of those is a trade
# worth making. Nothing here is or ever was a real credential.
_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def _credential(kind: str) -> str:
    return {
        "bearer": "Bear" + "er " + _ALPHABET,
        "openai_key": "sk" + "-" + _ALPHABET + "012345",
        "stripe_key": "sk" + "_live_" + "abcdefghijklmnop0123",
        "aws_key": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "connection_string": "postgresql" + "://user" + ":" + "pw" + "@db.internal:5432/orders",
        "jwt": "ey" + "J" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "assignment_password": "pass" + "word=" + "hunter2",
        "assignment_passwd": "pass" + "wd: " + "correcthorse",
        "assignment_api_key": "api" + "_key = " + "abc123",
        "assignment_access_token": "access" + "_token: " + "abc123",
        "assignment_secret": "sec" + "ret=" + "abc123",
    }[kind]


class TestCredentialHalf:
    """The union of the platform's detector and a local set — never one alone."""

    @pytest.mark.parametrize(
        "kind",
        ["bearer", "openai_key", "stripe_key", "aws_key", "connection_string", "jwt"],
    )
    def test_platform_shapes_are_caught(self, kind: str) -> None:
        """These are the formats the platform detector describes.

        They matter here because a value the platform catches and this gate
        misses is worse than a plain miss: the platform then raises from inside
        the node wrapper, the wrapper discards this node's entire delta —
        the clearing included — and the un-gated answer survives in state.
        """
        leak = _credential(kind)
        assert _security_gate_output(f"{CLEAN_ANSWER}\n{leak}") == VIOLATION_CREDENTIAL

    @pytest.mark.parametrize(
        "kind",
        [
            "assignment_password",
            "assignment_passwd",
            "assignment_api_key",
            "assignment_access_token",
            "assignment_secret",
        ],
    )
    def test_local_assignment_shapes_are_caught(self, kind: str) -> None:
        """Kept because the platform detector does NOT carry them.

        Its patterns describe credential FORMATS; none of them matches
        `password=…` at all. Replacing this set with the platform's would look
        like a tightening and would be a narrowing — which is a bypass.
        """
        from framework.security.credential_detector import detect_credentials

        leak = _credential(kind)
        assert detect_credentials(leak) == [], "this case is only worth keeping locally while the platform misses it"
        assert _security_gate_output(f"{CLEAN_ANSWER}\n{leak}") == VIOLATION_CREDENTIAL


class TestContactDetail:
    @pytest.mark.parametrize(
        "leak",
        [
            "連絡先 090-1234-5678",
            "お電話番号は09012345678です",
            "ご連絡先03-1234-5678",
            "080 1234 5678",
            "recipient.name@example.co.jp",
        ],
    )
    def test_contact_details_are_caught(self, leak: str) -> None:
        """The digit-run guards are lookarounds on non-digits, not \\b.

        Python's \\b is computed over \\w, which includes kana and kanji — so a
        phone number inside Japanese text, written without spaces as Japanese is,
        has no word boundary at either end and a \\b-anchored pattern silently
        does not match. The failing case is the ordinary one.
        """
        assert _security_gate_output(f"{CLEAN_ANSWER}\n{leak}") == VIOLATION_CONTACT_DETAIL


class TestPostalAddress:
    @pytest.mark.parametrize(
        "leak",
        [
            "お届け先: 123-4567 東京都渋谷区神南1-2-3",
            "お届け先 〒1234567東京都渋谷区",
            "お届け先 〒123-4567 大阪府大阪市北区1-2",
            "住所は東京都渋谷区神南1-2-3です",
            "Delivery to 123-4567 神奈川県横浜市西区2-3-4",
            "北海道札幌市中央区1-2 にお届けしました",
        ],
    )
    def test_full_addresses_are_caught(self, leak: str) -> None:
        """The shipped pattern was `\\d{3}-\\d{4}\\s*[都道府県]`, which required the
        prefecture SUFFIX character immediately after the postal code.

        Japanese addresses put it two characters later — `123-4567 東京都…` has 東
        in that position — so the pattern matched the form nobody writes and
        missed the form everybody writes. It was fail-open on its own headline
        case, and every test of it used the form it happened to match.
        """
        assert _security_gate_output(f"{CLEAN_ANSWER}\n{leak}") == VIOLATION_POSTAL_ADDRESS


class TestOrdinaryAnswersPassUntouched:
    @pytest.mark.parametrize(
        "text",
        [
            CLEAN_ANSWER,
            "遅延日数 / Days late: 12",
            "約6時間後のお届け予定です / Expected in about 6 hour(s)",
            "標準配達時間：ヤマト運輸は当日18:00まで、佐川急便は翌日12:00までの配達を目標としています。",
            "東京都内は当日配達の対象エリアです",
            "神奈川県にお住まいの場合は翌日配達となります",
            "追跡番号 1234-5678-9012 をご確認ください",
            "配達予定日は 2026-07-06 です",
            "再配達依頼 / Reschedule delivery",
            "STAR 2026 キャンペーン対象のお荷物です",
            "90d 以内のお荷物が対象です",
            "宅配ボックス 3-1 に配達しました",
        ],
    )
    def test_no_false_positive(self, text: str) -> None:
        """A prefecture name ALONE is deliberately not an address.

        "東京都内は当日配達です" is a sentence about a delivery window. A gate that
        refuses it has not been made stricter; it has stopped the agent doing the
        job it exists for, which is the failure mode that actually reaches
        customers.
        """
        assert _security_gate_output(text) is None

    def test_the_empty_answer_is_not_a_violation(self) -> None:
        assert _security_gate_output("") is None


class TestContainment:
    """A violating gate CLEARS the output-bearing fields. Raising is not containment."""

    def _violating_state(self) -> dict:
        return {
            "status": AgentStatus.SUCCESS.value,
            "result": f"{CLEAN_ANSWER}\nお届け先: 123-4567 東京都渋谷区神南1-2-3",
            "synthesized_response": "お届け先: 123-4567 東京都渋谷区神南1-2-3",
            "delivery_options": json.dumps(["再配達依頼"]),
            "order_last_event": "東京都渋谷区神南1-2-3 で配達",
            "estimated_arrival": "本日18:00",
            "escalation_reason": "東京都渋谷区神南1-2-3 にて保留",
        }

    def test_every_output_bearing_field_is_present_and_empty(self) -> None:
        """Present, not absent: LangGraph merges a node's partial delta into
        state, so a field left out of the delta keeps whatever it held."""
        result = PostProcessNode().execute(self._violating_state())
        assert result["status"] == AgentStatus.ERROR
        for field in OUTPUT_BEARING_FIELDS:
            assert field in result, f"{field} absent from the delta — state keeps its old value"
            assert result[field] == ""

    def test_nothing_of_the_answer_survives_anywhere_in_the_delta(self) -> None:
        blob = json.dumps(PostProcessNode().execute(self._violating_state()), ensure_ascii=False)
        assert "渋谷区神南" not in blob
        assert "123-4567" not in blob

    def test_the_replacement_notice_is_truthy(self) -> None:
        """The framework resolves the caller's output as `formatted_output or
        result` with no status check, so a falsy replacement re-opens that
        fallback onto whatever survived in state."""
        result = PostProcessNode().execute(self._violating_state())
        assert result["formatted_output"]
        assert result["formatted_output"] == NOTICES[REASON_OUTPUT_WITHHELD]

    def test_a_clean_answer_is_released_byte_identical(self) -> None:
        """The control. Without it, a gate that withheld everything would pass
        every test above."""
        result = PostProcessNode().execute({"status": AgentStatus.SUCCESS.value, "result": CLEAN_ANSWER})
        assert result["status"] == AgentStatus.SUCCESS
        assert result["formatted_output"] == CLEAN_ANSWER


class TestErrorEnvelopeIsClosedSet:
    """What the caller sees on an error is drawn only from this module's constants."""

    SENTINEL = "boom: upstream said {'customer':'A. Tanaka','token':'" + _credential("openai_key") + "'}"

    def _paths(self) -> list[dict]:
        """Every path this node can return a non-success result on."""
        return [
            {
                "status": AgentStatus.ERROR.value,
                "error_log": [self.SENTINEL],
                "result": self.SENTINEL,
                "synthesized_response": self.SENTINEL,
            },
            {
                "status": AgentStatus.SUCCESS.value,
                "error_log": [self.SENTINEL],
                "result": f"{CLEAN_ANSWER}\n連絡先 090-1234-5678",
            },
            {
                "status": AgentStatus.SUCCESS.value,
                "error_log": [self.SENTINEL],
                "result": f"{CLEAN_ANSWER}\n{_credential('aws_key')}",
            },
        ]

    def test_no_error_log_text_reaches_the_caller_on_any_path(self) -> None:
        """error_log carries node-authored text and, wherever a node interpolates
        a caught exception, upstream response text too. Truncating or
        credential-redacting that is not a closed-set contract, so none of it is
        published — parameterised over every path rather than one of them."""
        for state in self._paths():
            result = PostProcessNode().execute(state)
            blob = json.dumps(result, ensure_ascii=False)
            assert "A. Tanaka" not in blob
            assert _credential("openai_key") not in blob
            assert "boom" not in blob
            assert "error_log" not in result

    def test_every_published_value_is_a_declared_constant(self) -> None:
        for state in self._paths():
            result = PostProcessNode().execute(state)
            assert result["formatted_output"] in NOTICES.values()
            assert result["status"] == AgentStatus.ERROR

    def test_violation_labels_are_the_declared_set(self) -> None:
        """The audit record gets an outcome signal, and the signal comes from a
        closed set too. The audit log is not a store for message content."""
        for leak, label in (
            (_credential("assignment_password"), VIOLATION_CREDENTIAL),
            ("連絡先 090-1234-5678", VIOLATION_CONTACT_DETAIL),
            ("東京都渋谷区神南1-2-3", VIOLATION_POSTAL_ADDRESS),
        ):
            verdict = _security_gate_output(leak)
            assert verdict == label
            assert verdict in VIOLATION_LABELS
