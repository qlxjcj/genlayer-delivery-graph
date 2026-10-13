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

Deployed on GenLayer Bradbury: **`0x89E3122891B99cC8c13094CBA462b19734a9F459`**
([explorer](https://explorer-bradbury.genlayer.com/address/0x89E3122891B99cC8c13094CBA462b19734a9F459))

---

## Why a graph

Most escrow primitives assume one deliverable with one verdict. Real paid work
rarely arrives in one piece: it arrives as parts with **dependencies**, and a
part that rests on a failed part was never deliverable at all. A plain escrow
cannot express that. A graph can, and the topological gate is what turns a set
of independent checks into a single coherent release decision.

---

## What the model does, and what it does not

**The model never decides anything.** There is no score, no opinion, no
"supports". Each node declares literal markers — `"build passed"`,
`"status 200"`, `"99.9%"` — and consensus is asked for exactly one thing: *the
verbatim span of the evidence where each marker appears*.

The code then decides, with a pure conjunction:

```
marker met  ⇔  marker found literally in the fetched text
            or  a quoted span that really occurs in the text and really carries it
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

Every node has `evidence_by`. `submit_evidence` refuses at or after it and
`settle` refuses before it, so the set of sources provably cannot grow between
the moment it is read and the moment money moves.

```
test_settle_before_the_freeze_is_refused
test_evidence_after_the_freeze_is_refused
test_late_evidence_cannot_change_an_earlier_outcome
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

| node | `OPEN` | `FROZEN` | `ACCEPTED` |
|---|---|---|---|
| | sources may be filed | the set is final | every marker found, dependencies accepted |
| | | | |
| | | `REJECTED` | a marker was not found, or no source could be read |
| | | `BLOCKED` | a dependency was not accepted |

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
| `submit_evidence(graph_id, node_id, source_url)` | files a source. The contract fetches it itself at settlement — nobody can hand it a made-up document |
| `settle(graph_id, node_id)` | judges the node and moves its tranche immediately |
| `cancel(graph_id)` | payer only, only before any evidence is filed; refunds the escrow whole |
| `close(graph_id)` | marks the graph finished once every node is settled |

**Views** — all return JSON strings, never a bare `list` or `dict`, because a
`float` in a return value breaks calldata encoding on chain while direct mode
stays green.

| method | returns |
|---|---|
| `get_graph(graph_id)` | config, totals, per-node summary |
| `get_node(graph_id, node_id)` | full node record including `markers`, `spans`, `met`, `reason` |
| `get_evidence(graph_id, node_id)` | the filed source URLs |
| `get_graph_count()` | for deriving the next id from a count delta |

**Bounds** — 6 nodes, 3 parents each, 5 markers, 4 sources per node, 2 evidence
items filed per node.

---

## Testing

52 direct-mode tests in `tests/direct/test_delivery_graph.py`, in seven groups
(A creation, B freeze boundary, C marker verification, D dependency gate,
E fund conservation, F uniform write preconditions, G views).

Direct mode does not track native value, so `conftest.py` installs two
accounting hooks — payable value into the contract, `emit_transfer` out of it —
so every payout assertion is a real balance change rather than a tautology.

There is deliberately **no catch-all model mock**: a call a test did not
anticipate fails rather than silently returning something plausible.

```
python -m pytest tests/direct -q        # 52 passed
```

---

## Live on chain

`fixtures/` holds two real evidence documents served from this repository. The
contract fetches them over the network like any other source, which makes the
on-chain walkthrough reproducible: `report-alpha.txt` carries both markers,
`report-charlie.txt` carries only the first.

The walkthrough was run end to end on Bradbury — `create_graph` with 1 GEN of
escrow, two `submit_evidence` calls, the freeze, two settlements in dependency
order, and `close`:

```
status          CLOSED
escrow          1000000000000000000
accepted_total  1000000000000000000
rejected_total  0
blocked_total   0
settled_count   2
nodes           n1=ACCEPTED, n2=ACCEPTED
reconciles      true

n1  ACCEPTED | every marker found | met=[true, true]
    spans=["Build\n\nThe build passed on 2026-01-05.", "Signed off by the release captain."]
n2  ACCEPTED | every marker found | met=[true]
    spans=["The build passed on 2026-01-05."]
```

The spans are copied character for character out of the text the contract itself
fetched, line breaks included — compare them against `fixtures/` and see that a
model could not have invented them. Check it yourself with
`genlayer call <address> get_graph '"g0"'`.

---

## Source integrity

Deployed and repository source are byte identical:

```
sha256  c8b713e74e92711dbe72696130bb890445825502963331c65cea25dc18f64954
```

Reproduce with `genlayer code <address> --rpc https://rpc-bradbury.genlayer.com`
and compare against `delivery_graph.py`.
