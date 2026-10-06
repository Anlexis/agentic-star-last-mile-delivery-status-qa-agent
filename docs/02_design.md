# Design — RET-C2-577 RetailLastMileDeliveryStatusQAAgent

## 1. What this agent does

A customer asks where their parcel is. The agent resolves the question to a
specific parcel, combines whatever delivery record the deployment holds for it
with its own delivery knowledge, and answers with the status, how late it is,
when it is now expected, what the customer can do next, and whether a human
representative is being brought in.

| | |
|---|---|
| Template ID | RET-C2-577 |
| Category | Cat 2 — a domain-specific pipeline for one job-to-be-done |
| Industry | RET (Retail) |
| Pattern | Retrieval over a domain knowledge base, deterministic composition |
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance |
| Architecture | Nested: an outer `AgentBaseGraph`, a `GraphNode` in its `main` slot, an inner `BaseGraph` |

No model is called. The answer is composed from the parcel's own status and
passages this repository owns, which is why the manifest declares
`generation_mode: deterministic` and requires no model extra.

## 2. Architecture

```
Outer backbone (AgentBaseGraph — add_edges() is not overridden):
  START -> initialize -> pre_process -> main -> {route} -> post_process -> finalize -> END

Inner delivery workflow (DomainWorkflowGraph — BaseGraph, linear):
  START -> query_normalize -> order_context_extract -> status_pattern_retrieve
        -> response_synthesize -> escalation_check -> output_format -> END
```

```
src/api/server.py                          HTTP adapter: authentication, size cap, credential screen
src/graph/graph.py                         outer graph and the main-slot node
src/graph/domain_workflow_graph.py         inner graph, the six domain steps
src/graph/context_bridge.py                the validated contract across the graph boundary
src/nodes/pre_process_node.py              the caller contract is admitted or refused here
src/nodes/post_process_node.py             the output boundary
src/nodes/query_normalize_node.py          inner step 1
src/nodes/order_context_extract_node.py    inner step 2
src/nodes/status_pattern_retrieve_node.py  inner step 3
src/nodes/response_synthesize_node.py      inner step 4
src/nodes/escalation_check_node.py         inner step 5
src/nodes/output_format_node.py            inner step 6
src/schemas/state.py                       the flat TypedDict both layers share
src/services/caller_contract.py            closed sets, bounds, inert alphabets, the request screen
src/services/runtime_config.py             the single reader of config/config.yaml
```

### 2.1 Why the request crosses the graph boundary on a side channel

`GraphNode.execute()` invokes the inner graph as
`subgraph.invoke(user_input, session_id=…, ctx=…)`. Only the request STRING
crosses — neither the outer state nor the caller's structured parameters are
forwarded. Without a bridge, the validated delivery records the entry node
produces would never reach the pipeline that needs them, and every answer would
come from the baseline while the adapter looked as though it had read them.

The two sanctioned subclass hooks bridge it: the main-slot node's
`extract_input()` stashes the validated contract on a ContextVar immediately
before the inner invoke, and the inner graph's `_extra_initial_state()` seeds it
into the inner state. Only the VALIDATED contract crosses; the raw request body
never travels.

Serialising the records into the request string instead is not viable. The
platform rewrites personal-data shapes out of that field at every node boundary,
and its name heuristic reads title-case proper nouns as personal names, so a
carrier or locker name would arrive masked.

## 3. The caller-data contract

A request carries a question (`input`) and, optionally, structured parameters
(`input_context`). The structured half is the delivery records the deployment
already holds — the rows of an order-management snapshot, not anything the
customer types.

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

| Field | Rule |
|---|---|
| `channel` | closed set: `line`, `chat`, `email` |
| `orders` | at most 20 entries |
| `orders[].order_reference` | `[a-z0-9_]{1,32}` — required, and the only caller value that renders |
| `orders[].carrier` | closed set: `yamato`, `sagawa`, `japan_post`, `custom` |
| `orders[].status_code` | closed set: `IN_TRANSIT`, `DELAYED`, `DELIVERED`, `UNKNOWN` — required |
| `orders[].last_event_code` | closed set of scan-event codes; the WORDING is looked up here, never sent |
| `orders[].delay_days` | finite, `0 … 365` |
| `orders[].eta_hours` | finite, `0 … 8760` |

Three properties follow from that table, and the rest of the design rests on
them.

**No caller text reaches an answer.** A carrier's own event wording never
travels; the caller sends a CODE and `src/services/caller_contract.py` supplies
the description. The only caller value that renders is the parcel reference, on
an alphabet with no whitespace and no punctuation a reader could mistake for
formatting — so there is no newline a request could use to make a forged line
look like a quoted delivery rule.

**Every number is finite.** `float("nan") >= 3.0` is False, as is every other
comparison against NaN, so a NaN delay would report itself as inside every
threshold and the escalation this agent exists to raise would never fire, with
nothing failing anywhere. Non-finite values are refused, not clamped.

**Unknown keys are refused, not ignored.** Ignoring leaves the key in
`state["input_context"]`, which the backbone's first node copies verbatim into
its own result, where the platform's mandatory output scan runs. One
credential-shaped string in an undeclared key ends the run at node one with a
traceback and no explanation.

A refusal names a field and a reason from a closed set, and never repeats the
value. The field path is built only from this repository's own names and integer
indices: a caller key is not echoed, because a key name is caller data too and
can itself be credential-shaped.

## 4. State

Shared fields (`user_input`, `input_context`, `validated_input`, `result`,
`formatted_output`, `status`, `session_id`, `node_history`, `error_log`) are
inherited from `AgentState`. Structured values are serialised as JSON strings:
State is checkpointed with msgpack, and a nested mapping there is where
corruption starts.

| Field | Type | Written by | Meaning |
|---|---|---|---|
| `delivery_contract` | `Optional[str]` | PreProcessNode | the validated contract, JSON |
| `escalation_threshold_days` | `float` | inner graph seed | days late before a person is brought in |
| `max_kb_chunks` | `int` | inner graph seed | cap on passages quoted in one answer |
| `output_channel` | `Optional[str]` | inner graph seed | caller's channel, or the configured default |
| `carrier_scope_config` | `Optional[str]` | inner graph seed | carriers this deployment serves, JSON |
| `normalized_query` | `Optional[str]` | QueryNormalizeNode | the question, lower-cased and collapsed |
| `order_reference` | `Optional[str]` | QueryNormalizeNode | the parcel, on the inert alphabet |
| `carrier_hint` | `Optional[str]` | QueryNormalizeNode | carrier named in the question, if in scope |
| `order_context_source` | `Optional[str]` | OrderContextExtractNode | `caller_record` or `baseline` |
| `order_status_code` | `Optional[str]` | OrderContextExtractNode | the resolved status |
| `order_last_event` | `Optional[str]` | OrderContextExtractNode | scan description, from our catalogue |
| `order_delay_days` | `float` | OrderContextExtractNode | days past the expected date |
| `estimated_arrival` | `Optional[str]` | OrderContextExtractNode | arrival estimate, in words |
| `escalating_event` | `bool` | OrderContextExtractNode | the scan event always warrants a person |
| `retrieved_chunks` | `Optional[str]` | StatusPatternRetrieveNode | quoted passages, JSON |
| `synthesized_response` | `Optional[str]` | ResponseSynthesizeNode | the composed explanation |
| `escalation_required` | `bool` | EscalationCheckNode | whether a person is being brought in |
| `escalation_reason_code` | `Optional[str]` | EscalationCheckNode | which rule fired |
| `escalation_reason` | `Optional[str]` | EscalationCheckNode | the same, for the customer |
| `delivery_options` | `Optional[str]` | OutputFormatNode | self-service options, JSON |
| `formatted_output` | `Optional[str]` | PreProcessNode, PostProcessNode | what the caller receives |

`formatted_output` must be declared here: the framework filters `invoke()`'s
result to the keys this TypedDict names, and a key not listed is dropped without
a word.

## 5. Nodes

### 5.1 Backbone

| Node | Slot | Trust level | Responsibility |
|---|---|---|---|
| `PreProcessNode` | `pre_process` | `VERIFIED_EXTERNAL` | admit or refuse the request and its structured parameters |
| `DeliveryStatusGraphNode` | `main` | inherited | run the inner workflow; bridge the contract; map the result back |
| `PostProcessNode` | `post_process` | `ANONYMOUS` | the output boundary |

### 5.2 Inner steps

All declare `TrustLevel.ANONYMOUS`. The caller's level was already checked at the
backbone's external boundary and the invocation context crosses unchanged;
declaring `VERIFIED_EXTERNAL` here would deny nothing extra and would break the
pipeline the moment this agent were composed inside another one, where a
`VERIFIED_EXTERNAL` caller cannot satisfy an `INTERNAL` inner node.

| Node | Key | Responsibility |
|---|---|---|
| `QueryNormalizeNode` | `query_normalize` | extract the parcel reference and the carrier hint |
| `OrderContextExtractNode` | `order_context_extract` | resolve to a caller record, or degrade to the baseline |
| `StatusPatternRetrieveNode` | `status_pattern_retrieve` | select the delivery-knowledge passages |
| `ResponseSynthesizeNode` | `response_synthesize` | compose the explanation |
| `EscalationCheckNode` | `escalation_check` | decide whether a person is needed |
| `OutputFormatNode` | `output_format` | render for the channel; attach options and notice |

Node `execute()` methods take exactly one argument. The framework's node wrapper
calls `execute(state)`, so a node declaring `config=None` as well would read its
default on every single invocation while looking configurable — which is why
runtime configuration reaches them through seeded state instead.

### 5.3 Resolved or baseline

`OrderContextExtractNode` matches the question's parcel reference against the
caller's records, folding the separators a customer types onto the stored key, so
`TRK-8291847` and `trk_8291847` are the same parcel. When nothing matches — or
nothing was sent — the answer is built from delivery knowledge alone and says so
in the response. A customer told "we have no record of this parcel" can act; one
handed a confident-sounding UNKNOWN cannot tell the two situations apart.

## 6. The output boundary

One invariant, stated and enforced:

> A released delivery answer carries no credential-shaped string, no recipient
> contact detail, and no full Japanese delivery address.

**There is no monetary precision grid in this template.** It answers "where is my
parcel", renders no amounts and no aggregates, so a rounding gate would have
nothing to round. The invariant above is what takes its place, and it is enforced
for every representation rather than the convenient ones.

| Half | What it covers |
|---|---|
| Credential | the union of the platform's own detector and a local set |
| Contact detail | Japanese mobile and landline numbers, hyphenated or run together; e-mail addresses |
| Postal address | a postal code with a prefecture close behind it, in any way the code is written; or a prefecture followed by a municipality and a street block |

The credential half is a UNION on purpose. Delegating entirely to the platform
would be a narrowing disguised as an upgrade: its patterns describe credential
FORMATS and match nothing of the `password=…` shape. Keeping only the local set
is the other bypass: it has no AWS key id, no Stripe key, no connection string —
and a value the platform catches and this gate misses is worse than a plain miss,
because the platform then raises from inside the node wrapper, the wrapper
discards this node's entire delta, and the un-gated answer survives in state.

The digit-run guards are lookarounds on non-digits rather than `\b`. Python's
`\b` is computed over `\w`, which includes kana and kanji, so a phone number
inside Japanese text — written without spaces, as Japanese is — has no word
boundary at either end and a `\b`-anchored pattern silently does not match.

A prefecture name alone is deliberately not an address. "東京都内は当日配達です" is a
sentence about a delivery window, and a gate that refuses it has not been made
stricter; it has stopped the agent doing its job.

**On a violation the boundary clears rather than raises.** The framework resolves
the caller's output as `formatted_output or result` with no status check, so a
gate that merely raises still ships the un-gated answer inside the error
envelope. Every output-bearing field is returned present and empty — a field left
out of a partial delta keeps whatever it held — and the replacement notice is a
constant, therefore truthy, because a falsy replacement re-opens the same
fallback.

The outer graph's `get_output()` adds the second half: on any non-success status
the output resolves to the boundary's own notice or to nothing, never to
`result`. No third layer re-scans the success path; one would contain a leak by
itself and thereby make the boundary's own scan unfalsifiable.

**The caller-visible error carries only constants from that module.**
`state["error_log"]` holds node-authored text and, wherever a node interpolates a
caught exception, upstream response text as well. Truncating or
credential-redacting that is not a closed-set contract, so none of it is
published. `error_log` stays as the internal channel the audit trail needs.

## 7. Entry point

`src/api/server.py` is an adapter and holds no domain logic. It:

- establishes the caller's trust level. When `INVOKE_AUTH_TOKEN` is set, a caller
  no upstream middleware vouched for must present it as a bearer token and runs
  at `VERIFIED_EXTERNAL`; middleware-established trust is never demoted. Without
  this the entry node's gate denies every request before any node runs, and
  `/health` stays green throughout;
- caps the serialised structured parameters at 256 KB;
- screens those parameters for credential shapes using the platform's own
  detector, and refuses with `400` naming the field. The request cannot succeed
  either way — the backbone's first node returns the parameters verbatim into its
  own result and the platform's output gate scans them — so this turns an opaque
  node-one failure into something the caller can act on. `400` and not `422`:
  pydantic owns `422` and answers there with a list of error objects;
- provisions the secret provider under the identity the manifest declares.
  `namespace` is `lower(industry)`, not the lowercased template id; a namespace
  that disagrees with the manifest splits one agent's secrets across two stores
  with no error at boot.

## 8. Configuration

`config/agent.yaml` is the manifest: identity, entry point, trust level, and the
compile-time `requires` lists. Both lists are empty, derived from the code —
nothing calls `ctx.secrets.require()` and no model client is constructed, and
declaring a secret that is not provisioned would make the agent fail at compile
time.

`config/config.yaml` holds the runtime parameters. `src/services/runtime_config.py`
is its only reader and every value passes a bounded parser there.

| Key | Effect |
|---|---|
| `max_retry`, `timeout_s` | backbone |
| `escalation_threshold_days` | days late before the answer routes to a person |
| `max_kb_chunks` | passages quoted in one answer |
| `default_channel` | rendering when the caller names no channel |
| `carrier_scope` | carriers this deployment answers for |

Each of the four delivery values visibly changes the released answer; none is
decorative. The domain subset is forwarded to the inner graph, which seeds it
into inner state — the only route a declared value has into a node.
