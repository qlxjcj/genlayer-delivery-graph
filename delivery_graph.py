# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
import json
from genlayer import *

MAX_NODES = 6
MAX_PARENTS = 3
MAX_MARKERS = 5
MAX_MARKER_LEN = 120
MAX_SOURCES = 4
MAX_EVIDENCE = 2
MAX_LABEL = 120

ACTIVE = "ACTIVE"
CLOSED = "CLOSED"
CANCELLED = "CANCELLED"

OPEN = "OPEN"
FROZEN = "FROZEN"
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
BLOCKED = "BLOCKED"

SPACE = " \t\r\n\f\v"


class DeliveryGraph(gl.Contract):
    """Escrow for multi-part deliverables: a graph of nodes, each released only
    when its markers are found and every dependency was accepted."""

    graphs: TreeMap[str, str]
    evidence: TreeMap[str, str]
    graph_count: u256

    def __init__(self):
        self.graph_count = 0

    def _addr_hex(self, a) -> str:
        if isinstance(a, str):
            return a
        h = getattr(a, "as_hex", None)
        if isinstance(h, str):
            return h
        if isinstance(a, (bytes, bytearray)):
            return "0x" + bytes(a).hex()
        return str(a)

    def _is_valid_address(self, addr) -> bool:
        if not isinstance(addr, str) or not addr.startswith("0x") or len(addr) != 42:
            return False
        try:
            int(addr[2:], 16)
        except ValueError:
            return False
        return True

    def _iso_to_epoch(self, s: str) -> int:
        # Hand-rolled: validators must agree, so no optional stdlib.
        s = s.strip()
        if len(s) < 19:
            return 0
        offset = 0
        tail = s[19:]
        if tail.endswith("Z"):
            tail = tail[:-1]
        if tail and (tail[0] == "+" or tail[0] == "-"):
            try:
                offset = (1 if tail[0] == "+" else -1) * (int(tail[1:3]) * 3600 + int(tail[4:6]) * 60)
            except (ValueError, IndexError):
                offset = 0
        try:
            y, mo, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
            hh, mm, ss = int(s[11:13]), int(s[14:16]), int(s[17:19])
        except (ValueError, IndexError):
            return 0
        if not (1 <= mo <= 12 and 1 <= d <= 31 and hh < 24 and mm < 60 and ss < 61):
            return 0
        yy = y - 1 if mo <= 2 else y
        era = yy // 400
        yoe = yy - era * 400
        doy = (153 * (mo + (-3 if mo > 2 else 9)) + 2) // 5 + d - 1
        doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
        return (era * 146097 + doe - 719468) * 86400 + hh * 3600 + mm * 60 + ss - offset

    def _now(self) -> int:
        raw = getattr(gl, "message_raw", None)
        d = None
        if raw is not None:
            try:
                d = raw["datetime"]
            except Exception:
                d = None
        if d is None:
            d = getattr(gl.message, "datetime", None)
        if d is None:
            return 0
        return self._iso_to_epoch(d if isinstance(d, str) else str(d))

    def _norm(self, text: str) -> str:
        return " ".join(str(text).lower().split())

    def _squash(self, text: str) -> str:
        """Lowercase with every blank stripped, so a marker survives a renderer
        wrapping it."""
        out = []
        for ch in str(text).lower():
            if ch in SPACE:
                continue
            out.append(ch)
        return "".join(out)

    def _decode_body(self, content) -> str:
        body = getattr(content, "body", None)
        if body is None:
            return str(content)
        return body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)

    def _transfer(self, recipient: Address, amount: int):
        if amount > 0:
            gl.get_contract_at(recipient).emit_transfer(value=u256(amount))

    def _load_graph(self, graph_id: str) -> dict:
        raw = self.graphs.get(str(graph_id), "")
        if not raw:
            raise gl.vm.UserError("Graph not found")
        return json.loads(raw)

    def _save_graph(self, g: dict):
        self.graphs[g["graph_id"]] = json.dumps(g)

    def _find_node(self, g: dict, node_id: str) -> dict:
        for n in g["nodes"]:
            if str(n["id"]) == str(node_id):
                return n
        raise gl.vm.UserError("Node not found")

    def _load_node(self, graph_id: str, node_id: str):
        g = self._load_graph(graph_id)
        return g, self._find_node(g, node_id)

    def _require_active(self, g: dict):
        if str(g["status"]) != ACTIVE:
            raise gl.vm.UserError("Graph is " + str(g["status"]))

    def _ev_key(self, graph_id: str, node_id: str) -> str:
        return str(graph_id) + "|" + str(node_id)

    def _load_evidence(self, key: str) -> list:
        raw = self.evidence.get(key, "")
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def _marker_met(self, bodies: list, span: str, marker: str) -> bool:
        # The model never decides, it only quotes. Each source is checked in
        # full and on its own, so none can crowd another out of the set.
        m = self._squash(marker)
        if not m or not any(self._squash(b) for b in bodies):
            return False
        # Fast path: the marker is literally there, so no quote is needed.
        if any(self._norm(marker) in self._norm(b) for b in bodies):
            return True
        # The quote counts only if it really occurs in a fetched body and really
        # carries the marker, so a model can only lose credit, never create it.
        s = self._squash(span)
        if not s:
            return False
        return m in s and any(s in self._squash(b) for b in bodies)

    def _as_spans(self, result, count: int) -> list:
        out = []
        if isinstance(result, dict):
            raw = result.get("spans")
            if isinstance(raw, list):
                for i in range(count):
                    v = raw[i] if i < len(raw) else ""
                    out.append(str(v) if isinstance(v, str) else "")
        while len(out) < count:
            out.append("")
        return out

    def _examine(self, urls: list, markers: list) -> dict:
        """Fetch the sources and quote each marker out of them. One equivalence
        block per settlement; a literal marker never reaches the model."""
        count = len(markers)
        if not urls:
            return {"retrieved": False, "met": [False] * count, "spans": [""] * count}

        def check() -> dict:
            bodies = []
            read = 0
            for u in urls:
                text = ""
                try:
                    text = self._decode_body(gl.nondet.web.render(u))
                except Exception:
                    text = ""
                if text.strip():
                    read += 1
                bodies.append(text)
            if read == 0:
                return {"retrieved": False, "met": [False] * count, "spans": [""] * count}
            # Every source gets the same fixed share of the prompt, so none can
            # push another out of what the model is shown.
            shown = "\n\n".join(["SOURCE " + str(k + 1) + ":\n" + bodies[k][:700]
                                 for k in range(len(bodies))])
            task = (
                "Locate text in the SOURCES. Decide nothing. For each MARKER return the\n"
                "one span carrying it, copied in the same character order, or an empty\n"
                "string if no source carries it. Never invent text.\n\n" +
                shown + "\n\nMARKERS:\n" +
                "\n".join([str(i + 1) + ". " + str(m) for i, m in enumerate(markers)]) +
                '\n\nJSON only: {"spans": ["...", ...]} with exactly ' +
                str(count) + " entries in order."
            )
            # An answer the contract cannot use credits nothing, and the node
            # still ends: unverifiable evidence never releases money.
            parsed = None
            try:
                raw = gl.nondet.exec_prompt(task)
                if isinstance(raw, str):
                    parsed = json.loads(raw.replace("```json", "").replace("```", ""))
                elif isinstance(raw, dict):
                    parsed = raw
            except Exception:
                parsed = None
            spans = self._as_spans(parsed, count)
            # The model supplies the quoting; the code decides on it, over every
            # fetched source in full and separately.
            met = [self._marker_met(bodies, spans[i], str(markers[i])) for i in range(count)]
            return {"retrieved": True, "met": met, "spans": spans}

        principle = (
            "Equivalent only if retrieved and every entry of met match exactly. "
            "The spans wording may differ, but empty stays empty."
        )
        return gl.eq_principle.prompt_comparative(check, principle)

    @gl.public.write.payable
    def create_graph(self, recipient: str, nodes: list) -> str:
        payer = self._addr_hex(gl.message.sender_address)
        escrow = int(gl.message.value)
        if not self._is_valid_address(recipient):
            raise gl.vm.UserError("Recipient must be 0x + 40 hex")
        if self._addr_hex(recipient).lower() == payer.lower():
            raise gl.vm.UserError("Recipient must differ from payer")
        if escrow <= 0:
            raise gl.vm.UserError("Escrow must be > 0")
        if not isinstance(nodes, list) or not nodes or len(nodes) > MAX_NODES:
            raise gl.vm.UserError("Nodes must be 1..6")

        now = self._now()
        seen = []
        cfgs = []
        total = 0
        for n in nodes:
            if not isinstance(n, dict):
                raise gl.vm.UserError("Each node must be an object")
            nid = str(n.get("id", "")).strip()
            if not nid or len(nid) > 32 or nid in seen:
                raise gl.vm.UserError("Node ids must be unique and short")
            label = str(n.get("label", "")).strip()
            if not label or len(label) > MAX_LABEL:
                raise gl.vm.UserError("Node label required")

            parents = n.get("parents", [])
            if not isinstance(parents, list) or len(parents) > MAX_PARENTS:
                raise gl.vm.UserError("Parents must be 0..3 ids")
            plist = []
            for p in parents:
                pid = str(p).strip()
                # A parent must already exist, so the graph is acyclic by construction.
                if pid not in seen or pid in plist:
                    raise gl.vm.UserError("Parents must be earlier and distinct")
                plist.append(pid)

            markers = n.get("markers", [])
            if not isinstance(markers, list) or not markers or len(markers) > MAX_MARKERS:
                raise gl.vm.UserError("Markers must be 1..5")
            mlist = []
            for m in markers:
                ms = str(m).strip()
                if not ms or len(ms) > MAX_MARKER_LEN:
                    raise gl.vm.UserError("Markers must be short and non-empty")
                if self._squash(ms) in [self._squash(x) for x in mlist]:
                    raise gl.vm.UserError("Markers must be distinct")
                mlist.append(ms)

            sources = n.get("sources", [])
            if not isinstance(sources, list) or not sources or len(sources) > MAX_SOURCES:
                raise gl.vm.UserError("Sources must be 1..4")
            slist = []
            for u in sources:
                us = str(u).strip()
                if not (us.startswith("http://") or us.startswith("https://")) or us in slist:
                    raise gl.vm.UserError("Sources must be distinct http(s)")
                slist.append(us)

            tranche = int(n.get("tranche", 0))
            window = int(n.get("window", 0))
            if tranche <= 0:
                raise gl.vm.UserError("Tranche must be > 0")
            if window <= 0:
                raise gl.vm.UserError("Window must be > 0")
            total += tranche
            seen.append(nid)
            cfgs.append({
                "id": nid, "label": label, "parents": plist, "markers": mlist,
                "sources": slist, "tranche": tranche, "evidence_by": now + window,
                "status": OPEN, "reason": "", "spans": [], "met": [], "judged": [],
            })

        # Every unit of the escrow is spoken for before a single one can move.
        if total != escrow:
            raise gl.vm.UserError("Sum of tranches must equal escrow exactly")

        gid = "g" + str(int(self.graph_count))
        self.graphs[gid] = json.dumps({
            "graph_id": gid, "payer": payer, "recipient": self._addr_hex(recipient),
            "escrow": escrow, "created_at": now, "status": ACTIVE,
            "node_count": len(cfgs), "settled_count": 0,
            "accepted_total": 0, "rejected_total": 0, "blocked_total": 0,
            "nodes": cfgs,
        })
        self.graph_count = u256(int(self.graph_count) + 1)
        return gid

    @gl.public.write
    def submit_evidence(self, graph_id: str, node_id: str, source_url: str) -> str:
        g, node = self._load_node(graph_id, node_id)
        self._require_active(g)
        if str(node["status"]) != OPEN:
            raise gl.vm.UserError("Node is not open")
        # The set of sources closes here and cannot be touched afterwards.
        if self._now() >= int(node["evidence_by"]):
            raise gl.vm.UserError("Evidence window has closed")
        # Only the claimant may file. The payer filing would let the payer pick
        # the evidence its own escrow is judged on, and anyone else filing could
        # fill both slots with sources carrying nothing, forcing a rejection the
        # claimant never chose.
        if self._addr_hex(gl.message.sender_address).lower() != str(g["recipient"]).lower():
            raise gl.vm.UserError("Only the recipient files evidence")
        if str(source_url).strip() not in [str(u) for u in node["sources"]]:
            raise gl.vm.UserError("Source is not in this node's allowlist")
        key = self._ev_key(graph_id, node_id)
        items = self._load_evidence(key)
        if len(items) >= MAX_EVIDENCE:
            raise gl.vm.UserError("Too many evidence items")
        if str(source_url).strip() in items:
            raise gl.vm.UserError("Source already filed")
        items.append(str(source_url).strip())
        self.evidence[key] = json.dumps(items)
        return "accepted"

    @gl.public.write
    def settle(self, graph_id: str, node_id: str) -> str:
        g, node = self._load_node(graph_id, node_id)
        self._require_active(g)
        if str(node["status"]) not in (OPEN, FROZEN):
            raise gl.vm.UserError("Node already settled")
        # Nothing is judged until the set of sources is final.
        if self._now() < int(node["evidence_by"]):
            raise gl.vm.UserError("Evidence window is still open")
        for pid in node["parents"]:
            if str(self._find_node(g, pid)["status"]) in (OPEN, FROZEN):
                raise gl.vm.UserError("Dependency " + str(pid) + " is not settled")

        node["status"] = FROZEN
        node["spans"] = []
        node["met"] = []
        amount = int(node["tranche"])

        blocked_by = ""
        for pid in node["parents"]:
            if str(self._find_node(g, pid)["status"]) != ACCEPTED:
                blocked_by = str(pid)
                break

        if blocked_by:
            node["status"] = BLOCKED
            node["reason"] = "dependency " + blocked_by + " was not accepted"
            destination = str(g["payer"])
            g["blocked_total"] = int(g["blocked_total"]) + amount
        else:
            urls = self._load_evidence(self._ev_key(graph_id, node_id))
            if not urls:
                # Nothing was filed, so the node is judged on the complete set
                # configured at creation, never on an empty one.
                urls = [str(u) for u in node["sources"]]
            node["judged"] = urls
            markers = [str(m) for m in node["markers"]]
            res = self._examine(urls, markers)
            if not isinstance(res, dict):
                res = {}
            retrieved = bool(res.get("retrieved", False))
            met = [bool(x) for x in res.get("met", [])][:len(markers)]
            met += [False] * (len(markers) - len(met))
            node["met"] = met
            node["spans"] = self._as_spans(res, len(markers))

            if not retrieved:
                node["status"] = REJECTED
                node["reason"] = "no source could be read"
            elif all(met):
                node["status"] = ACCEPTED
                node["reason"] = "every marker found"
            else:
                node["status"] = REJECTED
                missed = markers[0]
                for i in range(len(met)):
                    if not met[i]:
                        missed = markers[i]
                        break
                node["reason"] = "not found: " + str(missed)[:80]

            if str(node["status"]) == ACCEPTED:
                destination = str(g["recipient"])
                g["accepted_total"] = int(g["accepted_total"]) + amount
            else:
                destination = str(g["payer"])
                g["rejected_total"] = int(g["rejected_total"]) + amount

        g["settled_count"] = int(g["settled_count"]) + 1
        self._save_graph(g)
        # The tranche moves now, so nothing is left sitting in the contract.
        self._transfer(Address(destination), amount)
        return str(node["status"])

    @gl.public.write
    def cancel(self, graph_id: str) -> str:
        g = self._load_graph(graph_id)
        self._require_active(g)
        if self._addr_hex(gl.message.sender_address).lower() != str(g["payer"]).lower():
            raise gl.vm.UserError("Only the payer cancels")
        # Only before anyone has filed evidence or been judged.
        for n in g["nodes"]:
            if str(n["status"]) != OPEN:
                raise gl.vm.UserError("A node has already been judged")
            if self._load_evidence(self._ev_key(graph_id, str(n["id"]))):
                raise gl.vm.UserError("Evidence already filed")
        g["status"] = CANCELLED
        self._save_graph(g)
        self._transfer(Address(g["payer"]), int(g["escrow"]))
        return CANCELLED

    @gl.public.write
    def close(self, graph_id: str) -> str:
        g = self._load_graph(graph_id)
        self._require_active(g)
        for n in g["nodes"]:
            if str(n["status"]) in (OPEN, FROZEN):
                raise gl.vm.UserError("Node " + str(n["id"]) + " is not settled")
        g["status"] = CLOSED
        self._save_graph(g)
        return CLOSED

    @gl.public.view
    def get_graph(self, graph_id: str) -> str:
        g = self._load_graph(graph_id)
        return json.dumps({
            "graph_id": g["graph_id"], "payer": g["payer"], "recipient": g["recipient"],
            "escrow": g["escrow"], "created_at": g["created_at"], "status": g["status"],
            "node_count": g["node_count"], "settled_count": g["settled_count"],
            "accepted_total": g["accepted_total"], "rejected_total": g["rejected_total"],
            "blocked_total": g["blocked_total"],
            "nodes": [{"id": n["id"], "parents": n["parents"], "tranche": n["tranche"],
                       "evidence_by": n["evidence_by"], "status": n["status"]} for n in g["nodes"]],
        })

    @gl.public.view
    def get_node(self, graph_id: str, node_id: str) -> str:
        g, node = self._load_node(graph_id, node_id)
        return json.dumps(node)

    @gl.public.view
    def get_evidence(self, graph_id: str, node_id: str) -> str:
        g, node = self._load_node(graph_id, node_id)
        return json.dumps(self._load_evidence(self._ev_key(graph_id, node_id)))

    @gl.public.view
    def get_graph_count(self) -> int:
        return int(self.graph_count)
