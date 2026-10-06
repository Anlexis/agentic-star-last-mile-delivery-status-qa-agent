"""AgentCore Platform v1.0"""

# The single reader of config/config.yaml.
#
# Before the flat manifest, runtime values lived in an `agent.config` block
# inside config/agent.yaml and the graph read them from there. The flat manifest
# has no such block, so that reader returned an empty mapping and every declared
# value was silently replaced by a node default — the file still parsed, the
# suite still passed, and nothing the file said had any effect. That is the
# failure mode this module exists to make impossible: there is one reader, it
# names the file it read, and every value it returns has been through a bounded
# parser.
#
# Bounds here are the same ones the caller contract applies to caller data, for
# the same reason. An operator editing a YAML file is not an attacker, but a
# NaN written by a broken template renderer compares False against every score,
# and the agent would answer as though nothing were ever late.

from __future__ import annotations

import os
from typing import Any, Dict, List

from framework.utils.config_loader import load_config

from src.services.caller_contract import CARRIERS, CHANNELS, ContractRefusal, finite_in_range, in_closed_set

# Repository root — this file is src/services/runtime_config.py.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(_ROOT, "config", "config.yaml")

# Defaults are the answer to "what does this agent do when the file is absent",
# not a second place to configure it. They match the values the shipped
# config/config.yaml declares, so a deployment that loses the file behaves as
# documented rather than differently-but-plausibly.
DEFAULTS: Dict[str, Any] = {
    "max_retry": 3,
    "timeout_s": 30,
    "escalation_threshold_days": 3.0,
    "max_kb_chunks": 4,
    "default_channel": "line",
    "carrier_scope": list(CARRIERS),
}

# The keys the graph forwards to the inner delivery pipeline. `max_retry` and
# `timeout_s` belong to the backbone and are not in this set.
DOMAIN_KEYS = ("escalation_threshold_days", "max_kb_chunks", "default_channel", "carrier_scope")


class ConfigurationError(Exception):
    """A declared runtime value is outside the range this agent accepts."""


def _validated(raw: Dict[str, Any]) -> Dict[str, Any]:
    config: Dict[str, Any] = dict(DEFAULTS)
    try:
        if "max_retry" in raw:
            # The backbone refuses max_retry >= 10 at compile time; refusing it
            # here names the file instead of raising from inside the framework.
            config["max_retry"] = int(finite_in_range(raw["max_retry"], field="max_retry", low=0, high=9))
        if "timeout_s" in raw:
            config["timeout_s"] = int(finite_in_range(raw["timeout_s"], field="timeout_s", low=1, high=3600))
        if "escalation_threshold_days" in raw:
            config["escalation_threshold_days"] = finite_in_range(
                raw["escalation_threshold_days"], field="escalation_threshold_days", low=0, high=365
            )
        if "max_kb_chunks" in raw:
            config["max_kb_chunks"] = int(finite_in_range(raw["max_kb_chunks"], field="max_kb_chunks", low=1, high=20))
        if "default_channel" in raw:
            config["default_channel"] = in_closed_set(raw["default_channel"], field="default_channel", allowed=CHANNELS)
        if "carrier_scope" in raw:
            scope = raw["carrier_scope"]
            if not isinstance(scope, list) or not scope:
                raise ConfigurationError("carrier_scope must be a non-empty list of carrier identifiers")
            checked: List[str] = [
                in_closed_set(entry, field=f"carrier_scope[{index}]", allowed=CARRIERS)
                for index, entry in enumerate(scope)
            ]
            config["carrier_scope"] = checked
    except ContractRefusal as refusal:
        # The file path and the field are operator-facing detail and safe to
        # name; this never runs on caller data.
        raise ConfigurationError(f"{CONFIG_PATH}: {refusal.field} — {refusal.reason}") from None
    return config


def runtime_config() -> Dict[str, Any]:
    """Read and validate config/config.yaml.

    Returns the full mapping the graph is constructed with, defaults filled in
    for anything the file does not declare.
    """
    raw = load_config(CONFIG_PATH) if os.path.exists(CONFIG_PATH) else {}
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{CONFIG_PATH}: top level must be a mapping")
    return _validated(raw)


def domain_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """The subset of *config* the inner delivery pipeline consumes."""
    merged = {**DEFAULTS, **(config or {})}
    return {key: merged[key] for key in DOMAIN_KEYS}
