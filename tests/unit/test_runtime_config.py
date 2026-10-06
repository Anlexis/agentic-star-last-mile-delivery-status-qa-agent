# config/config.yaml is read, validated, and reaches the pipeline.
#
# The failure this guards against is the quiet one. Before the flat manifest, the
# graph read its runtime values out of an `agent.config` block in the manifest;
# the flat manifest has no such block, so the reader returned an empty mapping
# and every declared value was replaced by a node default. The file parsed, the
# suite passed, and nothing the file said had any effect.
#
# So the assertions here are about liveness, not about shape: the shipped file is
# read, its values survive into the mapping the graph is constructed with, and a
# value outside the accepted range is refused rather than quietly replaced.

import pytest

from src.services import runtime_config as rc
from src.services.caller_contract import CARRIERS, CHANNELS


class TestShippedFile:
    def test_the_shipped_config_is_read_and_valid(self) -> None:
        config = rc.runtime_config()
        assert config["escalation_threshold_days"] == 3.0
        assert config["max_kb_chunks"] == 4
        assert config["default_channel"] in CHANNELS
        assert set(config["carrier_scope"]) <= set(CARRIERS)
        assert config["max_retry"] == 3
        assert config["timeout_s"] == 30

    def test_the_shipped_file_declares_every_domain_key(self) -> None:
        """A default is the answer to "the file is missing", not a second place
        to configure the agent. If the shipped file stopped declaring a key, this
        fails rather than letting the default quietly become the real value."""
        from framework.utils.config_loader import load_config

        raw = load_config(rc.CONFIG_PATH)
        for key in rc.DOMAIN_KEYS:
            assert key in raw, f"config/config.yaml no longer declares {key}"

    def test_domain_config_forwards_exactly_the_pipeline_keys(self) -> None:
        """max_retry and timeout_s belong to the backbone; forwarding them into
        the inner graph would put two owners on one value."""
        forwarded = rc.domain_config(rc.runtime_config())
        assert set(forwarded) == set(rc.DOMAIN_KEYS)
        assert "max_retry" not in forwarded
        assert "timeout_s" not in forwarded


class TestValidation:
    @pytest.mark.parametrize(
        "raw",
        [
            {"escalation_threshold_days": "NaN"},
            {"escalation_threshold_days": float("inf")},
            {"escalation_threshold_days": -1},
            {"escalation_threshold_days": 400},
            {"max_kb_chunks": 0},
            {"max_kb_chunks": "many"},
            {"max_retry": 10},
            {"timeout_s": 0},
            {"default_channel": "carrier_pigeon"},
            {"carrier_scope": []},
            {"carrier_scope": "yamato"},
            {"carrier_scope": ["dhl"]},
        ],
    )
    def test_an_out_of_range_declaration_is_refused(self, raw: dict) -> None:
        """A NaN threshold parses fine and then compares False against every
        delay, so the agent would decline to escalate anything, for ever, with
        nothing failing. Refusing at load names the file and the field."""
        with pytest.raises(rc.ConfigurationError) as error:
            rc._validated(raw)
        assert rc.CONFIG_PATH in str(error.value) or "carrier_scope" in str(error.value)

    def test_max_retry_is_refused_below_the_backbone_ceiling(self) -> None:
        """The backbone raises on max_retry >= 10 at compile time. Catching it
        here names config/config.yaml instead of surfacing from inside the
        framework with no indication of where the value came from."""
        assert rc._validated({"max_retry": 9})["max_retry"] == 9
        with pytest.raises(rc.ConfigurationError):
            rc._validated({"max_retry": 10})

    def test_an_undeclared_key_falls_back_to_its_default(self) -> None:
        config = rc._validated({})
        assert config == rc.DEFAULTS
