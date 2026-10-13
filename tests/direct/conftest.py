"""Fixtures for Delivery Graph direct-mode tests.

Direct mode does not track native value flow, so two accounting hooks are
installed (mirroring what the chain does) so the tests can genuinely assert
fund movement:

1. payable value: `create_graph` moves `vm.value` sender -> contract
2. `emit_transfer` is intercepted as a PostMessage gl_call and moved
   contract -> recipient

Without them every payout assertion would be a tautology.

Time is driven explicitly with `vm.warp(iso)` so each node's evidence freeze
boundary is tested deterministically. Every test states exactly what the model
is expected to quote, so an unexpected model call fails loudly.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "delivery_graph.py",
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)

PAYER = "0x" + "aa" * 20
RECIPIENT = "0x" + "bb" * 20
RECIPIENT_B = "0x" + "cc" * 20
STRANGER = "0x" + "44" * 20
BAD_ADDR = "0x1234"

ESCROW = 10 ** 18

# --- markers a node demands in its evidence ---
M_BUILD = "build passed"
M_STATUS = "status 200"
M_SIGNED = "release captain"
M_PCT = "99.9%"
M_TAIL = "final-marker"

# --- evidence bodies. The report token is what both web and model mocks key
# on, and no token is a substring of another, so first-match-wins cannot
# bleed one fixture into another. ---
BODY_ALPHA = ("alpha-report 412. The build passed on 2026-01-05. "
              "Endpoint /api/v2 returned status 200 at 09:14. "
              "Signed off by the release captain.")
# A renderer that dropped a stray blank inside a word: the literal path
# cannot see "build passed" here, only a quoted span can.
BODY_BRAVO = ("bravo-report 7. The buil d passed on 2026-01-05. "
              "Endpoint /api/v2 returned status 200 at 09:14.")
BODY_CHARLIE = "charlie-report 3. The build passed on 2026-01-05. Nothing else to report."
BODY_DELTA = "delta-report 9. Weather was sunny all week at the depot yard."
BODY_ECHO = "echo-report 11. Uptime held at 99.9% and latency stayed under 120ms."
# The value sits in the very last character of the body.
BODY_FOXTROT = "foxtrot-report 13. Nothing much happened. final-marker"
BODY_EMPTY = ""
BODY_ERROR = "Internal Server Error"

URL_ALPHA = "https://alpha.example.com/doc"
URL_BRAVO = "https://bravo.example.com/doc"
URL_CHARLIE = "https://charlie.example.com/doc"
URL_DELTA = "https://delta.example.com/doc"
URL_ECHO = "https://echo.example.com/doc"
URL_FOXTROT = "https://foxtrot.example.com/doc"
URL_EMPTY = "https://empty.example.com/doc"
URL_ERROR = "https://error.example.com/doc"
URL_UNKNOWN = "https://not-allowed.example.com/doc"


def iso(offset: int) -> str:
    return (BASE + timedelta(seconds=offset)).isoformat()


def balance(vm, addr):
    return vm._balances.get(vm._to_bytes(addr), 0)


def contract_balance(vm):
    return vm._balances.get(vm._contract_address, 0)


def spans_json(spans):
    return json.dumps({"spans": spans})


def remock(vm, llm=None):
    """Install web mocks plus exactly the model answers the test expects.

    There is deliberately no catch-all model mock: a call the test did not
    anticipate must fail rather than silently return something plausible."""
    vm.clear_mocks()
    for token, status, body in (
        ("alpha", 200, BODY_ALPHA),
        ("bravo", 200, BODY_BRAVO),
        ("charlie", 200, BODY_CHARLIE),
        ("delta", 200, BODY_DELTA),
        ("echo", 200, BODY_ECHO),
        ("foxtrot", 200, BODY_FOXTROT),
        ("empty", 200, BODY_EMPTY),
        ("error", 500, BODY_ERROR),
    ):
        vm.mock_web(".*" + token + "\\.example\\.com.*",
                    {"method": "GET", "status": status, "body": body})
    for pattern, response in (llm or []):
        vm.mock_llm(pattern, response)


def mock_spans(vm, token, spans):
    """Declare what the model should quote for the evidence it will be shown.

    Patterns must be mutually exclusive: when a node has two sources the prompt
    carries both bodies, so register a single pattern for the pair."""
    vm.mock_llm(".*" + token + "-report.*", spans_json(spans))


def node(nid, markers, tranche, window, parents=None, sources=None, label=None):
    return {
        "id": nid,
        "label": label or ("node " + nid),
        "parents": list(parents or []),
        "markers": list(markers),
        "sources": list(sources) if sources is not None else [URL_ALPHA],
        "tranche": tranche,
        "window": window,
    }


@pytest.fixture
def graph(direct_vm, direct_deploy):
    vm = direct_vm

    def _value_transfer_hook(vm, request):
        if "PostMessage" not in request:
            return None
        msg = request["PostMessage"]
        amount = int(msg.get("value", 0))
        if amount > 0:
            contract = vm._contract_address
            recipient = vm._to_bytes(msg["address"])
            vm._balances[contract] = vm._balances.get(contract, 0) - amount
            vm._balances[recipient] = vm._balances.get(recipient, 0) + amount
        return {"ok": None}

    vm._gl_call_hook = _value_transfer_hook

    remock(vm)
    c = direct_deploy(CONTRACT)

    def _wrap(fn):
        def inner(*args):
            if vm.value > 0:
                sender = vm._to_bytes(vm.sender)
                contract = vm._contract_address
                vm._balances[sender] = vm._balances.get(sender, 0) - vm.value
                vm._balances[contract] = vm._balances.get(contract, 0) + vm.value
            return fn(*args)
        return inner

    c.create_graph = _wrap(c.create_graph)

    for addr in (PAYER, RECIPIENT, RECIPIENT_B, STRANGER):
        vm.deal(addr, 1000 * ESCROW)

    vm.warp(iso(0))
    return vm, c


def make_graph(vm, c, nodes, escrow=None, recipient=RECIPIENT, sender=PAYER, value=None):
    vm.warp(iso(0))
    vm.sender = sender
    vm.value = value if value is not None else (
        escrow if escrow is not None else sum(int(n["tranche"]) for n in nodes))
    gid = c.create_graph(recipient, nodes)
    vm.value = 0
    return gid


def submit(vm, c, gid, nid, url, t):
    vm.warp(iso(t))
    vm.sender = STRANGER
    return c.submit_evidence(gid, nid, url)


def settle(vm, c, gid, nid, t):
    vm.warp(iso(t))
    vm.sender = STRANGER
    return c.settle(gid, nid)


def node_state(vm, c, gid, nid):
    return json.loads(c.get_node(gid, nid))


def graph_state(vm, c, gid):
    return json.loads(c.get_graph(gid))


def snapshot(vm, c, gid, nid):
    return (c.get_graph(gid), c.get_node(gid, nid), c.get_evidence(gid, nid))


def full_snapshot(vm, c, gid, nids):
    parts = [c.get_graph(gid)]
    for nid in nids:
        parts.append(c.get_node(gid, nid))
        parts.append(c.get_evidence(gid, nid))
    return parts
