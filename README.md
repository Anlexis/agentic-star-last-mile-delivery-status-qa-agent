# Last-Mile Delivery Status Q&A Agent

AI agent for answering last-mile delivery status questions for retail customer service, built with Agentic Star.

> **Category**: Cat 2 (a domain-specific pipeline for one job-to-be-done)
> **Industry**: Retail
> **Template ID**: RET-C2-577

## Overview

"Where is my parcel?" is the most common question a retail support desk gets, and
almost all of it is answerable without a person: the delivery record already
exists in the order-management system, and what the customer actually needs is
that record explained — what the status means, how late it is, when it is now
expected, and what they can do about it.

This agent takes a customer's question and whatever delivery records the
deployment holds, resolves the question to a specific parcel, and answers with
the status, the delay in days, an arrival estimate, the self-service options that
apply, and the delivery-policy passages the answer rests on. When the parcel
cannot be resolved, when a loss or damage has been reported, or when the delay
has reached the deployment's threshold, it routes the customer to a human
representative and says so in the answer.

Two design decisions are worth knowing before adapting it:

**No caller text reaches an answer.** A delivery record is sent as codes — a
carrier, a status, a scan event — and the wording for each is supplied by the
agent, not by the caller. The one caller-supplied value that renders is the
parcel reference, restricted to lower-case alphanumerics and underscores. So
there is no route by which a request's own words become part of a delivery
answer, and no newline that could make a forged line look like a quoted policy.

**No model is called.** The answer is composed from the parcel's own status and
passages that ship with the template. That makes every response reproducible and
every test deterministic, and it means adopting this template does not require a
model budget.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at graph
compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Sending a request

Delivery records belong in `input_context`, not in the request string. The
platform rewrites personal-data shapes out of the request string at every node
boundary, and its name heuristic reads title-case proper nouns — carrier names,
locker names — as personal names, so records embedded there arrive masked.

```json
{
  "input": "ヤマト運輸の TRK-8291847 はどこですか",
  "input_context": {
    "channel": "line",
    "orders": [
      {"order_reference": "trk_8291847", "carrier": "yamato",
       "status_code": "DELAYED", "last_event_code": "weather_delay",
       "delay_days": 4, "eta_hours": 48}
    ]
  }
}
```

Carriers, statuses, scan events and channels are closed sets; the parcel
reference is restricted to an inert alphabet; numbers must be finite and in
range; at most 20 records travel per request. A refused request names the field
that failed and never repeats the value. A request carrying no records is still
answered, from delivery knowledge alone, and the answer says so.

`docs/02_design.md` carries the full contract and what the output boundary enforces;
`docs/03_test_spec.md` lists the cases it is verified against.

## Project Structure

```
src/          agent implementation (nodes, graphs, services, schemas)
tests/        unit, boundary and integration tests
config/       agent manifest and runtime parameters
deploy/       local deployment recipe and a smoke payload
docs/         design specification and test specification
```

`docs/` holds the design (`02_design.md`) and the test specification
(`03_test_spec.md`).

## Customising

1. Adjust `config/config.yaml`: the escalation threshold, the passage cap, the
   default channel and the carrier scope all change the released answer.
2. Replace the delivery knowledge in `src/nodes/status_pattern_retrieve_node.py`
   with a retrieval call against your own carrier documentation. It ships as a
   small readable table so the shape of what belongs there is obvious.
3. Extend the scan-event catalogue and the closed sets in
   `src/services/caller_contract.py` to match your order-management system.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
