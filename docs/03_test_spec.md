# Test specification — RET-C2-577

280 tests, all deterministic: no model, no network, no clock. The suite runs
against the framework wheel the build pipeline installs.

```
python -m pytest tests/ -v
```

## What the tests are for

A green suite is evidence about coverage, not about health. The previous suite
here ran 42 tests in 0.08 seconds and never touched the HTTP entry point: every
test built the graph itself and supplied the trust level, so it stayed green
while the deployed agent denied every single request. The layout below is
organised around the properties that were missing, not around the files.

| File | Holds |
|---|---|
| `tests/unit/test_caller_contract.py` | what a request may carry, and what happens to everything else |
| `tests/unit/test_output_boundary.py` | the released-answer invariant, in both directions |
| `tests/unit/test_runtime_config.py` | `config/config.yaml` is read, bounded, and live |
| `tests/unit/test_agent.py` | each node driven directly, no framework wrapper in front |
| `tests/integration/test_invoke_e2e.py` | the real HTTP application, end to end |
| `tests/integration/test_manifest_identity_alignment.py` | the manifest and the entry point agree on who this agent is |
| `tests/proof_of_boundary/` | the framework-contract boundaries below |

## Caller input

| Property | Where |
|---|---|
| Every numeric field rejects NaN, ±Infinity, booleans, non-numerics and over-magnitude values — parameterised per field, inventoried from the contract rather than recalled | `test_caller_contract.py::TestFiniteInRange`, `::TestValidateInputContext` |
| Closed sets admit exactly their members | `::TestClosedSets` |
| The one caller value that renders is held to `[a-z0-9_]{1,32}` | `::TestInertIdentifier` |
| Chat-template control tokens are refused as a class — `<\|…\|>`, `[INST]`, `<<SYS>>`, `<s>`, `<system>` | `::TestDisallowedInstructionScreen` |
| A directive re-assembled out of markup or glyph-less characters is refused, in both reconstructions | same |
| Keys are screened as well as values, depth-first, after parsing | same |
| Ordinary delivery questions containing the same words are untouched | same |
| A refusal names a field and a closed-set reason, and never repeats the value | `::TestValidateInputContext`, `test_agent.py::TestPreProcessNode` |
| A field path carries only names this repository chose | `::TestValidateInputContext` |
| Structural caps: at most 20 records, 2000 characters of question, 256 KB of parameters | `::TestValidateInputContext`, `test_invoke_e2e.py::TestSizeCap` |
| The screens are enforced by the template's own node, proved by calling `execute()` directly | `test_agent.py::TestPreProcessNode` |

## The public path does real work

| Property | Where |
|---|---|
| An authenticated request gets a real answer through the real application | `test_invoke_e2e.py::TestTheAgentCanServeARequest` |
| An unauthenticated one is refused at the boundary with `401`, identically however the credential was wrong | same |
| The caller's records reach the inner pipeline — which they do not do on their own | `::TestTheAnswerDependsOnTheRequest` |
| Two very different delays move the rendered figure and flip the escalation | same |
| Every status path is reachable | same |
| Absent records degrade to a narrower answer that says so | same |
| A credential-shaped value on the parameter channel is refused with the field named, `400` not `422`, value never echoed | `::TestStructuredParameterCredentialScreen` |
| The adapter's screen blocks exactly what the platform gate blocks — asserted as a property over a set of payloads, not a sample | same |

## The output boundary

| Property | Where |
|---|---|
| Platform credential formats are caught | `test_output_boundary.py::TestCredentialHalf` |
| Assignment shapes the platform does NOT carry are caught, and the test asserts the platform misses them so the local set is not quietly dropped | same |
| Japanese contact details are caught, including the un-spaced form a `\b` pattern misses | `::TestContactDetail` |
| Full addresses are caught in each way a postal code is written, and with the code absent | `::TestPostalAddress` |
| Ordinary answers pass byte-identical — a prefecture name alone is not an address | `::TestOrdinaryAnswersPassUntouched` |
| On a violation every output-bearing field is present and empty, and the notice is truthy | `::TestContainment` |
| A clean answer is still released — the control | same |
| The caller-visible error is drawn only from module constants, over every non-success path | `::TestErrorEnvelopeIsClosedSet` |
| A sentinel seeded in `error_log` appears nowhere in the returned mapping | same |
| End to end: a leak injected on the DATA path is contained, with no traceback and no source paths in the envelope | `test_invoke_e2e.py::TestContainment` |

Credential-shaped fixtures are assembled at run time rather than written as
literals. The repository's own credential scan reads every file in the tree,
tests included, and weakening a blocking gate to accommodate a fixture is the
wrong trade.

## Configuration is live

| Property | Where |
|---|---|
| The shipped `config/config.yaml` is read and valid | `test_runtime_config.py::TestShippedFile` |
| It still declares every key the pipeline consumes, so a default cannot quietly become the real value | same |
| An out-of-range or non-finite declaration is refused, naming the file | `::TestValidation` |
| Lowering the passage cap quotes fewer passages | `test_agent.py::TestStatusPatternRetrieveNode` |
| Raising the escalation threshold suppresses an escalation for the same parcel | `::TestEscalationCheckNode` |
| Narrowing the carrier scope stops a carrier being claimed | `::TestQueryNormalizeNode` |

## Framework contract

| ID | Property | Where |
|---|---|---|
| TC-06 | `_security_gate_input()` is not overridden | `tests/unit/test_framework_compliance_tc06_tc07.py` |
| TC-07 | `_security_gate_output()` is not overridden | same |
| PB-2 / PB-5 | State is msgpack-safe and carries no credential-named field | `tests/proof_of_boundary/test_state_safety.py` |
| PB-4 | No platform SDK import anywhere under `src/` | `tests/proof_of_boundary/test_import_isolation.py` |
| PB-6 | Backbone order: initialize → pre_process → main → post_process → finalize | `tests/proof_of_boundary/test_pb_invoke_order.py` |
| PB-7 | Human-review interrupt propagation — skipped, this agent does not enable it | `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` |
| — | Node `execute()` takes exactly `state` | `tests/unit/test_agent.py::TestNodeContracts` |
| — | The committed deploy payload is the same request PB-6 proves is answerable | `tests/proof_of_boundary/test_pb_invoke_order.py` |

That last row exists because nothing used to hold the two together. A deployment
payload the entry node refuses produces a well-formed `200` with an agent-level
error inside it: every surrounding assertion passes, the pipeline is green, and
the only record is one line in an artifact nobody opens.

## Mutation results

Each fix was verified by faulting the DATA path, not the gate, and confirming the
suite fails. Baseline: 285 passed, 2 skipped.

| Mutant | Failures | What it proves |
|---|---|---|
| The entire pre-migration `src/` restored | **77** of 114 collectable | the suite is load-bearing against the code as it shipped |
| Adapter no longer establishes a trust level | 25 | the deployed agent could not serve a request at all |
| Finite parser accepts NaN and the infinities | 17 | a non-finite delay fails open on the decision this agent exists for |
| Control-token screen removed from the request check | 14 | the platform scores `<<SYS>>` as no finding; this screen is the one that catches it |
| Context bridge carries nothing | 12 | the records do not reach the pipeline on their own |
| Address pattern reverted to the shipped one | 12 | the shipped pattern was fail-open on its own headline case |
| Platform detector dropped from the credential half | 7 | the local set alone misses AWS, Stripe and connection strings |
| Local patterns dropped in favour of the platform detector | 6 | "delegating" narrows the gate — the platform matches no `password=…` shape |
| Graph config no longer reaches the pipeline | 4 | the declared runtime values are live, not decorative |
| Unknown context keys ignored instead of refused | 4 | ignoring is not dropping |
| Replacement notice made falsy | 3 | a falsy replacement re-opens the framework's `formatted_output or result` fallback |
| Clearing removed AND notice made falsy | 4 | the two together are what contain the answer |
| Output boundary stops clearing the output-bearing fields | 1 | see below |
| Contact guards reverted to `\b` | 1 | the un-spaced Japanese number is the only case `\b` misses, and it is the ordinary one |

Two of these are worth stating plainly rather than leaving as a number.

**The clearing is falsifiable only at unit level.** Removing it fails one test —
the one that asserts it directly. End to end the leak stays contained anyway,
because the replacement notice is truthy and the graph's `get_output()` refuses
to fall back to `result` on a non-success status. Each layer alone is sufficient,
so each one alone is unfalsifiable through the caller channel; removing both
together (4 failures, including the full-invoke containment test) is what shows
the pair is load-bearing. More defence can buy less assurance, and saying which
mutant proves which layer is the only honest way to report it.

**Restoring the pre-migration `src/` leaves three test modules unimportable**,
because they test modules that did not exist before the migration
(`test_caller_contract.py`, `test_runtime_config.py`, `test_output_boundary.py`).
pytest reports those as collection errors, which says nothing about detection.
The 77 above is measured with those three ignored, across the 114 tests that
still collect — including every end-to-end test through the real application.
