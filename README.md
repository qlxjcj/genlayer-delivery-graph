# Delivery Graph Escrow

**Dependency-gated release for multi-part deliverables.**

A payer funds a piece of work that only makes sense as a set of connected parts
— a feature with its tests, a grant with its milestones, a build with its
sign-offs. The contract holds the money as a **graph of work nodes**. Each node
carries its own escrow tranche, its own evidence window and a list of literal
markers it demands in its evidence.

A node is released to the recipient only when **every marker is found in the
evidence the contract fetched for it, and every node it depends on was
accepted**. Otherwise its tranche goes back to the payer.

```
                 ┌────────────┐        ┌────────────┐
                 │  n1 build  │        │  n2 tests  │
                 │  tranche A │        │  tranche B │
                 └─────┬──────┘        └─────┬──────┘
                       │ accepted            │ accepted
                       └──────────┬──────────┘
                                  ▼
                          ┌──────────────┐
                          │  n3 sign-off │
                          │   tranche C  │
                          └──────────────┘
```

`n3` cannot be settled until `n1` and `n2` are settled, and it is only released
if both were **accepted**. If either fails, `n3` is `BLOCKED` and tranche C
returns to the payer — work that rests on failed work is not paid for.

Deployed on GenLayer Bradbury: **`0x9901868ea78704b4488a123078228B0215E72858`**
([explorer](https://explorer-bradbury.genlayer.com/address/0x9901868ea78704b4488a123078228B0215E72858))

---

## Why a graph

Most escrow primitives assume one deliverable with one verdict. Real paid work
rarely arrives in one piece: it arrives as parts with **dependencies**, and a
part that rests on a failed part was never deliverable at all. A plain escrow
cannot express that. A graph can, and the topological gate is what turns a set
of independent checks into a single coherent release decision.

---

## Who chooses what gets judged

This is the part a reviewer should look at first, because it is the part that
was wrong once and is now closed three separate ways.

A node may configure up to **four** sources but only **two** can be filed. That
asymmetry was the bug: filing was open to anyone, so a third party could fill
both slots with sources that carry nothing, cut the real evidence out of the
judged set, and force a rejection the claimant never chose.

**1. Filing is the claimant's alone.** `submit_evidence` requires
`sender == recipient`. The recipient is the party making the claim, so choosing
the claim is theirs. The *payer* filing is the worst case of all — it would let
the payer pick the evidence its own escrow is judged on — and a payer call
reverts on chain (see the live section). Any other account reverts too.

**2. The judged set can never be the empty set, and filing can only ever narrow
a set that was fixed at creation.** If nothing was filed, or the payer declined
to, `settle` reads the **complete source list configured at creation**. So the
claimant's own inaction cannot shrink the judged set either, and the fail-closed
default is the full set rather than nothing.

**3. Every source is read in full, by itself.** Sources used to be concatenated
and truncated, so a long source listed first could push the real evidence out of
the text the decision was made on — a second, quieter exclusion vector of the
same shape. Each source is now fetched and kept separately, the literal search
runs over each body **in full**, and the model prompt gives every source the same
fixed share so none can crowd another out.

`get_node` returns the `judged` list — the exact URLs that were read — so a
reviewer can compare it against `sources` on chain and see the two match.

```
test_a_stranger_cannot_file_evidence_at_all
test_a_stranger_cannot_exhaust_the_slots_either
test_the_attack_does_not_work_end_to_end
test_a_hostile_caller_cannot_change_what_is_judged
test_a_decoy_source_cannot_crowd_the_real_one_out
test_an_unusable_answer_never_releases_the_tranche
```

---

## The one trust boundary

The payer picks the sources at creation, so the payer picks **where** the markers
are looked for. That is the point of the contract — the payer is the party
defining acceptance — and it is public: `get_node` shows the whole list before
any work starts, so a claimant can read it and decline the job.

No source can hurt either. A marker counts if it appears in **any** fetched body,
so adding, editing or listing a source can only ever help the claimant; there is
no way to use one source to push a node from accepted to rejected.

The residual is a payer who lists only venues they themselves control and then
blanks all of them, which fails `retrieved` and returns the escrow. That is
visible in the configured list at creation, before any work is done, and it is
why a list of several independent venues is the safer configuration.

---

## What the model does, and what it does not

**The model never decides anything.** There is no score, no opinion, no
"supports". Each node declares literal markers — `"build passed"`,
`"status 200"`, `"99.9%"` — and consensus is asked for exactly one thing: *the
verbatim span of the evidence where each marker appears*.

The code then decides, with a pure conjunction:

```
marker met  ⇔  marker found literally in any fetched body, in full
            or  a quoted span that really occurs in a body and really carries it
node accepted  ⇔  every marker met  ∧  every dependency accepted
```

The second clause is the model's one genuine contribution, and it is narrow: a
renderer that drops a stray blank inside a word (`"buil d passed"`) defeats a
literal search, and a verbatim quote catches it. The quote is then checked
against the text the contract itself fetched, so **a model can only lose credit,
never create it**. An answer the contract cannot use credits nothing at all.

Fetching and quoting share one equivalence principle block, so there is a
single consensus point per settlement, and the verdict fields are computed in
code inside that block — validators agree on the decision, not on the wording of
a leader's answer.

---

## Three invariants

These are the properties the whole design is arranged around. Each one is a
test group, not a claim.

### 1. The evidence set is final before anything is judged

The set is fixed twice over. It is written at creation, and it is filed once
only — into at most two slots, by one account, before `evidence_by`.
`submit_evidence` refuses at or after that point and `settle` refuses before it,
so the set provably cannot grow between the moment it is read and the moment
money moves. If nothing was filed at all, `settle` falls back to the full
configured set, so it cannot shrink to nothing either.

```
test_evidence_is_filed_while_the_window_is_open
test_settle_before_the_freeze_is_refused
test_evidence_after_the_freeze_is_refused
test_late_evidence_cannot_change_an_earlier_outcome
test_no_filing_at_all_judges_the_full_configured_set
```

### 2. Every write enforces the same preconditions

All five writes load the same record and check the same things: the graph is
`ACTIVE`, the node exists, the node's own state allows the action, and — for
settlement — every dependency is settled. **A refusal never mutates anything**,
which is asserted byte for byte.

```
test_every_refusal_leaves_the_state_byte_identical
test_the_graph_goes_read_only_once_closed
test_the_graph_goes_read_only_once_cancelled
test_a_settled_node_cannot_be_settled_again
```

### 3. No unit of the escrow is ever stranded

`create_graph` refuses unless `sum(tranches) == escrow` exactly, so the split is
settled before a single unit can move. Each node's tranche then has exactly one
destination: the recipient if accepted, the payer if rejected or blocked. At
`close` the contract balance is back where it started.

```
test_every_tranche_reaches_exactly_one_destination
test_no_money_is_left_behind_when_everything_fails
test_the_totals_reconcile_with_the_disbursements
test_cancel_refunds_the_full_escrow
```

`accepted_total + rejected_total + blocked_total == escrow` is asserted on a
graph where all four outcomes happen at once.

---

## Lifecycle

| node | meaning |
|---|---|
| `OPEN` | sources may still be filed, by the recipient alone |
| `FROZEN` | the set is final, judgement in progress |
| `ACCEPTED` | every marker found, dependencies accepted → tranche to recipient |
| `REJECTED` | a marker was not found, or no source could be read → tranche to payer |
| `BLOCKED` | a dependency was not accepted → tranche to payer |

| graph | meaning |
|---|---|
| `ACTIVE` | normal |
| `CLOSED` | every node settled; the escrow is fully disbursed |
| `CANCELLED` | the payer withdrew before anyone filed evidence; escrow refunded whole |

Settlement order is enforced by state, not by time: `settle(n)` requires every
parent of `n` to already be settled, and refuses otherwise.

---

## API

**Writes**

| method | note |
|---|---|
| `create_graph(recipient, nodes)` | **payable**. `nodes[i]` = `{id, label, parents, markers, sources, tranche, window}` |
| `submit_evidence(graph_id, node_id, source_url)` | **recipient only**, before `evidence_by`, at most 2, from the node's own allowlist. The contract fetches the source itself at settlement — nobody can hand it a made-up document |
| `settle(graph_id, node_id)` | judges the node and moves its tranche immediately. Permissionless |
| `cancel(graph_id)` | payer only, only before any evidence is filed; refunds the escrow whole |
| `close(graph_id)` | marks the graph finished once every node is settled |

**Views** — all return JSON strings, never a bare `list` or `dict`, because a
`float` in a return value breaks calldata encoding on chain while direct mode
stays green.

| method | returns |
|---|---|
| `get_graph(graph_id)` | config, totals, per-node summary |
| `get_node(graph_id, node_id)` | full node record including `markers`, `sources`, `judged`, `spans`, `met`, `reason` |
| `get_evidence(graph_id, node_id)` | the filed source URLs (empty when nothing was filed) |
| `get_graph_count()` | for deriving the next id from a count delta |

**Bounds** — 6 nodes, 3 parents each, 5 markers, 4 sources per node, 2 evidence
items filed per node.

---

## Testing

58 direct-mode tests in `tests/direct/test_delivery_graph.py`, in eight groups
(A creation, B freeze boundary, C marker verification, D dependency gate,
E fund conservation, F uniform write preconditions, G views, H adversarial
filing).

Direct mode does not track native value, so `conftest.py` installs two
accounting hooks — payable value into the contract, `emit_transfer` out of it —
so every payout assertion is a real balance change rather than a tautology.

The `submit_evidence` default sender in the harness is the **recipient**, since
that is now the only account that may file; `settle` defaults to a stranger,
since judgement stays permissionless. The adversarial group passes an explicit
`sender` to prove the refusal holds for the payer and for third parties alike.

There is deliberately **no catch-all model mock**: a call a test did not
anticipate fails rather than silently returning something plausible.

```
python -m pytest tests/direct -q        # 58 passed
```

---

## Live on chain

`fixtures/` holds two real evidence documents served from this repository. The
contract fetches them over the network like any other source, which makes the
on-chain walkthrough reproducible: `report-alpha.txt` carries `build passed` and
`status 200`, `report-charlie.txt` carries only `build passed`.

The walkthrough ran on Bradbury against the deployed contract, with each node
configured with **four** sources (the same two documents over two independent
hosts) and escrow of 1 GEN split 0.4 / 0.6 across two nodes.

**The corrected behaviour, observed on chain.** The payer attempted to file
evidence on `n1`:

```
submit_evidence(g0, n1, report-charlie.txt) as the PAYER
  -> FINISHED_WITH_ERROR                      (the write reverts)
  -> get_evidence(g0, n1) == []               (the attempt changed nothing)
```

And then the judged set, read back from the contract:

```
n1  ACCEPTED | every marker found
    configured sources 4   judged sources 4        <- not narrowed
    met   [true,true]
    spans ["The build passed on 2026-01-05.",
           "Endpoint /api/v2 returned status 200 at 09:14."]
    filed []                                       <- the attacker filed nothing
```

The spans are copied character for character out of the text the contract itself
fetched, line breaks and all — compare them against `fixtures/` and see that a
model could not have invented them.

The graph then ran to completion, `n1` before `n2` because of the dependency
gate:

```
g0  CLOSED | settled 2 of 2
    escrow          1000000000000000000
    accepted_total  1000000000000000000
    rejected_total  0
    blocked_total   0
    reconciles      true

n1  ACCEPTED  tranche 400000000000000000  judged 4/4  met [true,true]
n2  ACCEPTED  tranche 600000000000000000  judged 4/4  met [true]
    spans ["release captain"]
```

Every node was judged on all four of its configured sources, every marker was
found, and the escrow came out exactly whole: nothing was stranded in the
contract, and nothing was stranded in the judged set either.

Check it yourself with
`genlayer call 0x9901868ea78704b4488a123078228B0215E72858 get_graph '"g0"'`.

---

## Source integrity

Deployed and repository source are byte identical:

```
sha256  dd6fc641e3266f399ad09bacb2df29b8ddd72df7678fdef68632c76aa4638476
bytes   19927
```

Reproduce with `genlayer code 0x9901868ea78704b4488a123078228B0215E72858 --rpc https://rpc-bradbury.genlayer.com`
and compare against `delivery_graph.py`.
