"""AgentCore Platform v1.0"""

# Caller-request bridge across the outer/inner graph boundary.
#
# Why it exists: the framework invokes a nested graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)`. Only the request
# STRING crosses — neither the outer state nor the caller's structured
# invocation parameters are forwarded. So the validated delivery records the
# pre_process node produces would never reach the pipeline that needs them, and
# the agent would answer every question from its own stub baseline while looking
# as though it had read the caller's data.
#
# The two sanctioned subclass hooks bridge it:
#
#   DeliveryStatusGraphNode.extract_input(state)  [runs BEFORE subgraph.invoke]
#       -> set_delivery_contract(<validated contract>)
#   DomainWorkflowGraph._extra_initial_state()    [runs INSIDE subgraph.invoke]
#       -> seeds the contract into the inner state
#
# What crosses is the VALIDATED contract only: every record has already passed
# its closed-set and bounded-number checks, and every value that will render has
# already been held to an inert alphabet. The raw request body never travels.
#
# Serialising the records into the request string instead is not viable. The
# platform rewrites personal-data shapes out of that field at every node
# boundary, and its name heuristic reads title-case proper nouns as personal
# names — so a carrier name or a locker name would arrive masked. This channel
# is not rewritten.
#
# A ContextVar keeps the hand-off correct per thread and per task, so concurrent
# invocations inside one process cannot see each other's request.

from contextvars import ContextVar
from typing import Any, Dict, Optional

_DELIVERY_CONTRACT: ContextVar[Optional[Dict[str, Any]]] = ContextVar("ret_c2_577_delivery_contract", default=None)


def set_delivery_contract(contract: Optional[Dict[str, Any]]) -> None:
    """Stash the validated caller contract for the imminent inner-graph invoke."""
    _DELIVERY_CONTRACT.set(dict(contract) if contract else {})


def get_delivery_contract() -> Dict[str, Any]:
    """Read (without consuming) the stashed contract; {} when none was set."""
    return _DELIVERY_CONTRACT.get() or {}
