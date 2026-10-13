"""Direct-mode tests for Delivery Graph.

Groups A-G map onto the three lessons this contract is built around:

  A  creation and configuration        -- the conservation identity is enforced
                                          before a single unit can move
  B  the evidence freeze boundary      -- nothing is judged while the set of
                                          sources can still grow
  C  marker verification               -- the model only quotes, the code decides
  D  the dependency gate               -- a node is only released after the work
                                          it rests on was accepted
  E  fund conservation                 -- every unit reaches exactly one address
  F  uniform write preconditions       -- every refusal leaves the state untouched
  G  views                             -- no float or list ever leaves the contract
"""

import json

import pytest

from conftest import (
    BAD_ADDR,
    ESCROW,
    M_BUILD,
    M_PCT,
    M_SIGNED,
    M_TAIL,
    PAYER,
    RECIPIENT,
    STRANGER,
    URL_ALPHA,
    URL_BRAVO,
    URL_CHARLIE,
    URL_DELTA,
    URL_ECHO,
    URL_EMPTY,
    URL_ERROR,
    URL_FOXTROT,
    URL_UNKNOWN,
    balance,
    contract_balance,
    full_snapshot,
    graph_state,
    iso,
    make_graph,
    mock_spans,
    node,
    node_state,
    remock,
    settle,
    snapshot,
    spans_json,
    submit,
)


# ==========================================================================
# A. creation and configuration
# ==========================================================================


def test_create_graph_escrows_the_whole_sum(graph):
    vm, c = graph
    before = contract_balance(vm)
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 3 * 10 ** 17, 100)])
    assert contract_balance(vm) == before + 3 * 10 ** 17
    g = graph_state(vm, c, gid)
    assert g["status"] == "ACTIVE"
    assert g["escrow"] == 3 * 10 ** 17
    assert g["node_count"] == 1
    assert g["settled_count"] == 0


def test_rejects_a_bad_recipient(graph):
    vm, c = graph
    for bad in (BAD_ADDR, PAYER, "0x" + "zz" * 20, ""):
        with pytest.raises(Exception):
            make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)], recipient=bad)


def test_rejects_zero_escrow(graph):
    vm, c = graph
    with pytest.raises(Exception):
        make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)], value=0)


def test_rejects_tranches_that_do_not_sum_to_escrow(graph):
    """The conservation identity is enforced before a single unit can move."""
    vm, c = graph
    nodes = [node("n1", [M_BUILD], 10 ** 17, 100), node("n2", [M_BUILD], 10 ** 17, 100)]
    for escrow in (3 * 10 ** 17, 10 ** 17, 0):
        with pytest.raises(Exception):
            make_graph(vm, c, nodes, escrow=escrow)
    gid = make_graph(vm, c, nodes, escrow=2 * 10 ** 17)
    assert graph_state(vm, c, gid)["escrow"] == 2 * 10 ** 17


def test_rejects_a_bad_node_list(graph):
    vm, c = graph
    for bad in ([], "nope", 42, [node("n%d" % i, [M_BUILD], 1, 100) for i in range(7)]):
        with pytest.raises(Exception):
            make_graph(vm, c, bad, escrow=1)


def test_rejects_duplicate_node_ids(graph):
    vm, c = graph
    nodes = [node("n1", [M_BUILD], 10 ** 17, 100), node("n1", [M_BUILD], 10 ** 17, 100)]
    with pytest.raises(Exception):
        make_graph(vm, c, nodes)


def test_rejects_a_parent_that_would_make_a_cycle(graph):
    """A parent must already exist, so the graph is acyclic by construction."""
    vm, c = graph
    with pytest.raises(Exception):
        make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100, parents=["n2"]),
                           node("n2", [M_BUILD], 10 ** 17, 100, parents=["n1"])])
    with pytest.raises(Exception):
        make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100, parents=["n1"])])
    with pytest.raises(Exception):
        make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100, parents=["ghost"])])
    with pytest.raises(Exception):
        make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100,
                                parents=["n2", "n2"]),
                           node("n2", [M_BUILD], 10 ** 17, 100)])


def test_rejects_bad_markers(graph):
    vm, c = graph
    for bad in ([], [""], ["   "], [M_BUILD, M_BUILD], [M_BUILD, "BUILD PASSED"],
                ["x"] * 6, ["m" * 200]):
        with pytest.raises(Exception):
            make_graph(vm, c, [node("n1", bad, 10 ** 17, 100)])


def test_rejects_bad_sources(graph):
    vm, c = graph
    for bad in ([], ["ftp://x.example.com/y"], [URL_ALPHA, URL_ALPHA], ["notaurl"]):
        with pytest.raises(Exception):
            make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100, sources=bad)])
    with pytest.raises(Exception):
        make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100,
                                sources=[URL_ALPHA, URL_BRAVO, URL_CHARLIE,
                                         URL_DELTA, URL_ECHO])])


def test_rejects_non_positive_tranche_or_window(graph):
    vm, c = graph
    with pytest.raises(Exception):
        make_graph(vm, c, [node("n1", [M_BUILD], 0, 100)])
    with pytest.raises(Exception):
        make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 0)])
    with pytest.raises(Exception):
        make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, -5)])


# ==========================================================================
# B. the evidence freeze boundary
# ==========================================================================


def test_evidence_is_filed_while_the_window_is_open(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    assert submit(vm, c, gid, "n1", URL_ALPHA, 99) == "accepted"
    assert json.loads(c.get_evidence(gid, "n1")) == [URL_ALPHA]


def test_evidence_after_the_freeze_is_refused(graph):
    """The set of sources closes and nothing can reopen it."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    for t in (100, 500, 10 ** 6):
        with pytest.raises(Exception):
            submit(vm, c, gid, "n1", URL_ALPHA, t)
    assert json.loads(c.get_evidence(gid, "n1")) == []


def test_settle_before_the_freeze_is_refused(graph):
    """Nothing is judged while the set can still grow."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    submit(vm, c, gid, "n1", URL_ALPHA, 50)
    before = snapshot(vm, c, gid, "n1")
    for t in (50, 99):
        with pytest.raises(Exception):
            settle(vm, c, gid, "n1", t)
    assert snapshot(vm, c, gid, "n1") == before


def test_late_evidence_cannot_change_an_earlier_outcome(graph):
    """A node judged on a frozen set cannot be reopened by better evidence."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD, M_SIGNED], 10 ** 17, 100,
                                  sources=[URL_CHARLIE, URL_ALPHA])])
    submit(vm, c, gid, "n1", URL_CHARLIE, 50)   # proves the build, not the sign-off
    remock(vm, llm=[(r".*charlie-report.*",
                     spans_json(["The build passed on 2026-01-05", ""]))])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert node_state(vm, c, gid, "n1")["reason"] == "not found: release captain"

    # the missing proof turns up late -- far too late
    with pytest.raises(Exception):
        submit(vm, c, gid, "n1", URL_ALPHA, 250)
    assert node_state(vm, c, gid, "n1")["status"] == "REJECTED"


def test_rejects_a_source_outside_the_allowlist(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100, sources=[URL_ALPHA])])
    for url in (URL_UNKNOWN, URL_CHARLIE, "", "notaurl"):
        with pytest.raises(Exception):
            submit(vm, c, gid, "n1", url, 50)
    assert json.loads(c.get_evidence(gid, "n1")) == []
    assert submit(vm, c, gid, "n1", URL_ALPHA, 50) == "accepted"


def test_rejects_duplicate_and_excess_evidence(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100,
                                  sources=[URL_ALPHA, URL_CHARLIE, URL_DELTA])])
    assert submit(vm, c, gid, "n1", URL_ALPHA, 10) == "accepted"
    with pytest.raises(Exception):
        submit(vm, c, gid, "n1", URL_ALPHA, 20)          # already filed
    assert submit(vm, c, gid, "n1", URL_CHARLIE, 30) == "accepted"
    with pytest.raises(Exception):
        submit(vm, c, gid, "n1", URL_DELTA, 40)          # MAX_EVIDENCE reached
    assert json.loads(c.get_evidence(gid, "n1")) == [URL_ALPHA, URL_CHARLIE]


# ==========================================================================
# C. marker verification -- the model quotes, the code decides
# ==========================================================================


def test_every_marker_found_releases_the_tranche(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD, M_SIGNED], 2 * 10 ** 17, 100)])
    submit(vm, c, gid, "n1", URL_ALPHA, 50)
    before = balance(vm, RECIPIENT)
    remock(vm, llm=[(r".*alpha-report.*",
                     spans_json(["The build passed", "release captain"]))])
    assert settle(vm, c, gid, "n1", 200) == "ACCEPTED"
    assert balance(vm, RECIPIENT) == before + 2 * 10 ** 17
    assert node_state(vm, c, gid, "n1")["met"] == [True, True]


def test_one_missing_marker_sends_the_tranche_back(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD, M_SIGNED], 2 * 10 ** 17, 100,
                                  sources=[URL_CHARLIE])])
    submit(vm, c, gid, "n1", URL_CHARLIE, 50)   # no mention of a release captain
    before = balance(vm, PAYER)
    remock(vm, llm=[(r".*charlie-report.*", spans_json(["The build passed", ""]))])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert balance(vm, PAYER) == before + 2 * 10 ** 17
    n = node_state(vm, c, gid, "n1")
    assert n["met"] == [True, False]
    assert n["reason"] == "not found: release captain"


def test_no_evidence_at_all_rejects_the_node(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert node_state(vm, c, gid, "n1")["reason"] == "no source could be read"


def test_a_quote_rescues_a_marker_broken_by_a_stray_blank(graph):
    """The one thing the model is genuinely for: a renderer dropped a blank
    inside a word, so the literal path cannot see the marker. A verbatim quote
    can -- and code then proves the quote really is in the evidence."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100, sources=[URL_BRAVO])])
    submit(vm, c, gid, "n1", URL_BRAVO, 50)
    remock(vm, llm=[(r".*bravo-report.*",
                     spans_json(["The buil d passed on 2026-01-05"]))])
    assert settle(vm, c, gid, "n1", 200) == "ACCEPTED"
    assert node_state(vm, c, gid, "n1")["met"] == [True]


def test_the_same_marker_without_a_quote_is_not_met(graph):
    """So it is not free: with no quote the marker is simply not there."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100, sources=[URL_BRAVO])])
    submit(vm, c, gid, "n1", URL_BRAVO, 50)
    remock(vm, llm=[(r".*bravo-report.*", spans_json([""]))])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert node_state(vm, c, gid, "n1")["met"] == [False]


def test_a_quoted_span_that_is_not_in_the_evidence_is_discarded(graph):
    """A model cannot invent a quote: it must occur in the evidence itself."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_SIGNED], 10 ** 17, 100, sources=[URL_CHARLIE])])
    submit(vm, c, gid, "n1", URL_CHARLIE, 50)
    remock(vm, llm=[(r".*charlie-report.*",
                     spans_json(["Signed off by the release captain"]))])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert node_state(vm, c, gid, "n1")["met"] == [False]


def test_a_real_quote_about_the_wrong_thing_is_discarded(graph):
    """A genuine span from the genuine source, but it is about the build and
    the marker is the rate: it earns nothing."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100, sources=[URL_BRAVO])])
    submit(vm, c, gid, "n1", URL_BRAVO, 50)
    remock(vm, llm=[(r".*bravo-report.*", spans_json(["status 200 at 09:14"]))])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert node_state(vm, c, gid, "n1")["met"] == [False]


def test_an_empty_span_never_credits_a_marker(graph):
    """The empty-string trap: '' is a substring of absolutely everything."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100, sources=[URL_DELTA])])
    submit(vm, c, gid, "n1", URL_DELTA, 50)     # carries nothing relevant
    remock(vm, llm=[(r".*delta-report.*", spans_json([""]))])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert node_state(vm, c, gid, "n1")["met"] == [False]


def test_a_marker_in_the_last_character_of_the_body_is_found(graph):
    """Boundary: a value pressed against the end of the body must not be lost."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_TAIL], 10 ** 17, 100, sources=[URL_FOXTROT])])
    submit(vm, c, gid, "n1", URL_FOXTROT, 50)
    remock(vm, llm=[(r".*foxtrot-report.*", spans_json(["final-marker"]))])
    assert settle(vm, c, gid, "n1", 200) == "ACCEPTED"
    assert node_state(vm, c, gid, "n1")["met"] == [True]


def test_a_percentage_marker_is_found(graph):
    """'%' is a unit suffix, not part of the number, or every rate is lost."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_PCT], 10 ** 17, 100, sources=[URL_ECHO])])
    submit(vm, c, gid, "n1", URL_ECHO, 50)
    remock(vm, llm=[(r".*echo-report.*", spans_json(["99.9%"]))])
    assert settle(vm, c, gid, "n1", 200) == "ACCEPTED"
    assert node_state(vm, c, gid, "n1")["met"] == [True]


def test_a_wrong_number_of_spans_is_fail_closed(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD, M_SIGNED], 10 ** 17, 100,
                                  sources=[URL_CHARLIE])])
    submit(vm, c, gid, "n1", URL_CHARLIE, 50)
    remock(vm, llm=[(r".*charlie-report.*",
                     spans_json(["The build passed on 2026-01-05"]))])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert node_state(vm, c, gid, "n1")["met"] == [True, False]


def test_a_malformed_model_answer_is_fail_closed(graph):
    """A shape the contract cannot use credits nothing, and the node still ends,
    so nobody's money is left waiting on a model that misbehaved."""
    for bad in ('{"spans": "oops"}', "not json at all", '{"spans": [7, 8]}', ""):
        vm, c = graph
        gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100,
                                      sources=[URL_BRAVO])])
        submit(vm, c, gid, "n1", URL_BRAVO, 50)      # only a quote can find this one
        remock(vm, llm=[(r".*bravo-report.*", bad)])
        assert settle(vm, c, gid, "n1", 200) == "REJECTED"
        assert node_state(vm, c, gid, "n1")["met"] == [False]


def test_two_sources_can_jointly_satisfy_the_markers(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD, M_SIGNED], 10 ** 17, 100,
                                  sources=[URL_CHARLIE, URL_ALPHA])])
    submit(vm, c, gid, "n1", URL_CHARLIE, 10)
    submit(vm, c, gid, "n1", URL_ALPHA, 20)
    remock(vm, llm=[(r".*charlie-report.*",
                     spans_json(["The build passed", "release captain"]))])
    assert settle(vm, c, gid, "n1", 200) == "ACCEPTED"
    assert json.loads(c.get_evidence(gid, "n1")) == [URL_CHARLIE, URL_ALPHA]


def test_a_second_source_can_carry_what_the_first_missed(graph):
    """One source errors out; the node is judged on the rest, and the error
    page itself is credited with nothing."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD, M_SIGNED], 10 ** 17, 100,
                                  sources=[URL_ERROR, URL_ALPHA])])
    submit(vm, c, gid, "n1", URL_ERROR, 10)
    submit(vm, c, gid, "n1", URL_ALPHA, 20)
    remock(vm, llm=[(r".*alpha-report.*",
                     spans_json(["The build passed", "release captain"]))])
    assert settle(vm, c, gid, "n1", 200) == "ACCEPTED"


def test_an_error_page_carries_no_marker(graph):
    """A failure page is real text, but it proves nothing."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100, sources=[URL_ERROR])])
    submit(vm, c, gid, "n1", URL_ERROR, 10)
    remock(vm, llm=[(r".*Internal Server Error.*", spans_json([""]))])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert node_state(vm, c, gid, "n1")["reason"] == "not found: build passed"


def test_no_source_readable_rejects_the_node(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100, sources=[URL_EMPTY])])
    submit(vm, c, gid, "n1", URL_EMPTY, 10)
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert node_state(vm, c, gid, "n1")["reason"] == "no source could be read"


# ==========================================================================
# D. the dependency gate
# ==========================================================================


def test_cannot_settle_before_a_dependency_is_settled(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100),
                             node("n2", [M_BUILD], 10 ** 17, 100, parents=["n1"])])
    submit(vm, c, gid, "n1", URL_ALPHA, 10)
    submit(vm, c, gid, "n2", URL_ALPHA, 10)
    remock(vm, llm=[(r".*alpha-report.*", spans_json(["The build passed"]))])
    with pytest.raises(Exception):
        settle(vm, c, gid, "n2", 200)               # n1 is still open
    assert settle(vm, c, gid, "n1", 200) == "ACCEPTED"
    assert settle(vm, c, gid, "n2", 210) == "ACCEPTED"


def test_a_rejected_dependency_blocks_the_dependent(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_SIGNED], 10 ** 17, 100, sources=[URL_CHARLIE]),
                             node("n2", [M_BUILD], 10 ** 17, 100, parents=["n1"])])
    submit(vm, c, gid, "n1", URL_CHARLIE, 10)       # no release captain here
    submit(vm, c, gid, "n2", URL_ALPHA, 10)
    remock(vm, llm=[(r".*charlie-report.*", spans_json([""]))])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert settle(vm, c, gid, "n2", 210) == "BLOCKED"
    assert node_state(vm, c, gid, "n2")["reason"] == "dependency n1 was not accepted"


def test_a_blocked_dependency_blocks_everything_below_it(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_SIGNED], 10 ** 17, 100, sources=[URL_CHARLIE]),
                             node("n2", [M_BUILD], 10 ** 17, 100, parents=["n1"]),
                             node("n3", [M_BUILD], 10 ** 17, 100, parents=["n2"])])
    submit(vm, c, gid, "n1", URL_CHARLIE, 10)
    remock(vm, llm=[(r".*charlie-report.*", spans_json([""]))])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert settle(vm, c, gid, "n2", 210) == "BLOCKED"
    assert settle(vm, c, gid, "n3", 220) == "BLOCKED"
    g = graph_state(vm, c, gid)
    assert [n["status"] for n in g["nodes"]] == ["REJECTED", "BLOCKED", "BLOCKED"]
    assert g["settled_count"] == 3


def test_an_accepted_dependency_releases_the_dependent(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100),
                             node("n2", [M_SIGNED], 10 ** 17, 100, parents=["n1"])])
    submit(vm, c, gid, "n1", URL_ALPHA, 10)
    submit(vm, c, gid, "n2", URL_ALPHA, 10)
    remock(vm, llm=[
        (r".*alpha-report.*", spans_json(["The build passed", "release captain"])),
    ])
    assert settle(vm, c, gid, "n1", 200) == "ACCEPTED"
    assert settle(vm, c, gid, "n2", 210) == "ACCEPTED"
    g = graph_state(vm, c, gid)
    assert [n["status"] for n in g["nodes"]] == ["ACCEPTED", "ACCEPTED"]


def test_a_blocked_node_sends_its_tranche_to_the_payer(graph):
    vm, c = graph
    T1, T2 = 10 ** 17, 5 * 10 ** 17
    gid = make_graph(vm, c, [node("n1", [M_SIGNED], T1, 100, sources=[URL_CHARLIE]),
                             node("n2", [M_BUILD], T2, 100, parents=["n1"])])
    submit(vm, c, gid, "n1", URL_CHARLIE, 10)
    remock(vm, llm=[(r".*charlie-report.*", spans_json([""]))])
    before_p, before_r = balance(vm, PAYER), balance(vm, RECIPIENT)
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert settle(vm, c, gid, "n2", 210) == "BLOCKED"
    assert balance(vm, RECIPIENT) == before_r
    assert balance(vm, PAYER) == before_p + T1 + T2


# ==========================================================================
# E. fund conservation
# ==========================================================================


def test_every_tranche_reaches_exactly_one_destination(graph):
    """The identity this contract is built to hold, on a graph where the four
    nodes take all four roads at once."""
    vm, c = graph
    T = [10 ** 17, 2 * 10 ** 17, 3 * 10 ** 17, 4 * 10 ** 17]
    nodes = [
        node("n1", [M_BUILD], T[0], 100),
        node("n2", [M_SIGNED], T[1], 100, sources=[URL_CHARLIE]),
        node("n3", [M_BUILD], T[2], 100, parents=["n2"]),
        node("n4", [M_BUILD], T[3], 100, parents=["n1"]),
    ]
    held = contract_balance(vm)
    gid = make_graph(vm, c, nodes)
    submit(vm, c, gid, "n1", URL_ALPHA, 10)
    submit(vm, c, gid, "n2", URL_CHARLIE, 10)       # no release captain
    submit(vm, c, gid, "n3", URL_ALPHA, 10)
    submit(vm, c, gid, "n4", URL_ALPHA, 10)
    remock(vm, llm=[
        (r".*alpha-report.*", spans_json(["The build passed", ""])),
        (r".*charlie-report.*", spans_json(["", ""])),
    ])
    before_p, before_r = balance(vm, PAYER), balance(vm, RECIPIENT)

    assert settle(vm, c, gid, "n1", 200) == "ACCEPTED"
    assert settle(vm, c, gid, "n2", 201) == "REJECTED"
    assert settle(vm, c, gid, "n3", 202) == "BLOCKED"
    assert settle(vm, c, gid, "n4", 203) == "ACCEPTED"

    assert balance(vm, RECIPIENT) == before_r + T[0] + T[3]
    assert balance(vm, PAYER) == before_p + T[1] + T[2]

    g = graph_state(vm, c, gid)
    assert g["accepted_total"] == T[0] + T[3]
    assert g["rejected_total"] == T[1]
    assert g["blocked_total"] == T[2]
    assert g["accepted_total"] + g["rejected_total"] + g["blocked_total"] == g["escrow"]

    assert c.close(gid) == "CLOSED"
    assert contract_balance(vm) == held


def test_no_money_is_left_behind_when_everything_fails(graph):
    vm, c = graph
    held = contract_balance(vm)
    gid = make_graph(vm, c, [node("n1", [M_SIGNED], 10 ** 17, 100, sources=[URL_CHARLIE]),
                             node("n2", [M_BUILD], 10 ** 17, 100, parents=["n1"])])
    assert settle(vm, c, gid, "n1", 200) == "REJECTED"
    assert settle(vm, c, gid, "n2", 210) == "BLOCKED"
    assert c.close(gid) == "CLOSED"
    assert contract_balance(vm) == held
    assert balance(vm, RECIPIENT) == 1000 * ESCROW


def test_the_totals_reconcile_with_the_disbursements(graph):
    vm, c = graph
    T = [7 * 10 ** 16, 11 * 10 ** 16, 13 * 10 ** 16]
    nodes = [node("n1", [M_BUILD], T[0], 100),
             node("n2", [M_SIGNED], T[1], 100, sources=[URL_CHARLIE]),
             node("n3", [M_PCT], T[2], 100, sources=[URL_ECHO])]
    gid = make_graph(vm, c, nodes)
    submit(vm, c, gid, "n1", URL_ALPHA, 10)
    submit(vm, c, gid, "n2", URL_CHARLIE, 10)
    submit(vm, c, gid, "n3", URL_ECHO, 10)
    remock(vm, llm=[
        (r".*alpha-report.*", spans_json(["The build passed"])),
        (r".*charlie-report.*", spans_json([""])),
        (r".*echo-report.*", spans_json(["99.9%"])),
    ])
    before_p, before_r = balance(vm, PAYER), balance(vm, RECIPIENT)
    for nid in ("n1", "n2", "n3"):
        settle(vm, c, gid, nid, 200)

    g = graph_state(vm, c, gid)
    assert balance(vm, RECIPIENT) - before_r == g["accepted_total"]
    assert balance(vm, PAYER) - before_p == g["rejected_total"] + g["blocked_total"]
    assert g["accepted_total"] + g["rejected_total"] + g["blocked_total"] == sum(T)


# ==========================================================================
# F. uniform write preconditions
# ==========================================================================


def test_cancel_is_payer_only(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    for sender in (STRANGER, RECIPIENT, ""):
        vm.warp(iso(10))
        vm.sender = sender
        with pytest.raises(Exception):
            c.cancel(gid)
    vm.sender = PAYER
    assert c.cancel(gid) == "CANCELLED"


def test_cancel_refunds_only_before_any_evidence(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    submit(vm, c, gid, "n1", URL_ALPHA, 10)
    vm.warp(iso(20))
    vm.sender = PAYER
    with pytest.raises(Exception):
        c.cancel(gid)
    assert graph_state(vm, c, gid)["status"] == "ACTIVE"


def test_cancel_refunds_the_full_escrow(graph):
    vm, c = graph
    total = 5 * 10 ** 17
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 3 * 10 ** 17, 100),
                             node("n2", [M_BUILD], 2 * 10 ** 17, 100)])
    before = balance(vm, PAYER)
    held = contract_balance(vm)
    vm.warp(iso(10))
    vm.sender = PAYER
    assert c.cancel(gid) == "CANCELLED"
    assert balance(vm, PAYER) == before + total
    assert contract_balance(vm) == held - total
    assert graph_state(vm, c, gid)["status"] == "CANCELLED"


def test_close_requires_every_node_settled(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100),
                             node("n2", [M_BUILD], 10 ** 17, 100)])
    submit(vm, c, gid, "n1", URL_ALPHA, 10)
    submit(vm, c, gid, "n2", URL_ALPHA, 10)
    remock(vm, llm=[(r".*alpha-report.*", spans_json(["The build passed"]))])
    settle(vm, c, gid, "n1", 200)
    with pytest.raises(Exception):
        c.close(gid)
    settle(vm, c, gid, "n2", 210)
    assert c.close(gid) == "CLOSED"


def test_the_graph_goes_read_only_once_closed(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    submit(vm, c, gid, "n1", URL_ALPHA, 10)
    remock(vm, llm=[(r".*alpha-report.*", spans_json(["The build passed"]))])
    settle(vm, c, gid, "n1", 200)
    assert c.close(gid) == "CLOSED"
    before = full_snapshot(vm, c, gid, ["n1"])
    with pytest.raises(Exception):
        submit(vm, c, gid, "n1", URL_CHARLIE, 210)
    with pytest.raises(Exception):
        settle(vm, c, gid, "n1", 210)
    with pytest.raises(Exception):
        c.cancel(gid)
    with pytest.raises(Exception):
        c.close(gid)
    assert full_snapshot(vm, c, gid, ["n1"]) == before


def test_the_graph_goes_read_only_once_cancelled(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    vm.warp(iso(10))
    vm.sender = PAYER
    c.cancel(gid)
    before = full_snapshot(vm, c, gid, ["n1"])
    with pytest.raises(Exception):
        submit(vm, c, gid, "n1", URL_ALPHA, 20)
    with pytest.raises(Exception):
        settle(vm, c, gid, "n1", 200)
    with pytest.raises(Exception):
        c.close(gid)
    assert full_snapshot(vm, c, gid, ["n1"]) == before


def test_a_settled_node_cannot_be_settled_again(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    submit(vm, c, gid, "n1", URL_ALPHA, 10)
    remock(vm, llm=[(r".*alpha-report.*", spans_json(["The build passed"]))])
    assert settle(vm, c, gid, "n1", 200) == "ACCEPTED"
    before = full_snapshot(vm, c, gid, ["n1"])
    with pytest.raises(Exception):
        settle(vm, c, gid, "n1", 210)
    assert full_snapshot(vm, c, gid, ["n1"]) == before


def test_every_refusal_leaves_the_state_byte_identical(graph):
    """All five writes enforce the same preconditions, and none of them
    mutates anything when it refuses."""
    vm, c = graph
    nodes = [node("n1", [M_BUILD], 10 ** 17, 100),
             node("n2", [M_BUILD], 10 ** 17, 100, parents=["n1"])]
    gid = make_graph(vm, c, nodes)
    submit(vm, c, gid, "n1", URL_ALPHA, 10)

    cases = [
        ("submit after the freeze", lambda: submit(vm, c, gid, "n2", URL_ALPHA, 200)),
        ("submit off the allowlist", lambda: submit(vm, c, gid, "n2", URL_UNKNOWN, 10)),
        ("submit a source twice", lambda: submit(vm, c, gid, "n1", URL_ALPHA, 20)),
        ("submit to a missing node", lambda: submit(vm, c, gid, "ghost", URL_ALPHA, 10)),
        ("settle before the freeze", lambda: settle(vm, c, gid, "n1", 50)),
        ("settle with an unsettled dependency", lambda: settle(vm, c, gid, "n2", 200)),
        ("settle a missing node", lambda: settle(vm, c, gid, "ghost", 200)),
    ]
    for label, call in cases:
        before = full_snapshot(vm, c, gid, ["n1", "n2"])
        with pytest.raises(Exception):
            call()
        assert full_snapshot(vm, c, gid, ["n1", "n2"]) == before, label

    for sender, call in ((PAYER, lambda: c.cancel(gid)),
                         (STRANGER, lambda: c.close(gid))):
        vm.sender = sender
        before = full_snapshot(vm, c, gid, ["n1", "n2"])
        with pytest.raises(Exception):
            call()
        assert full_snapshot(vm, c, gid, ["n1", "n2"]) == before


# ==========================================================================
# G. views
# ==========================================================================


def test_views_return_strings_never_structures(graph):
    """A bare float or list in a return value breaks calldata encoding on chain
    while direct mode stays green, so the type is part of the contract."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    for value in (c.get_graph(gid), c.get_node(gid, "n1"), c.get_evidence(gid, "n1")):
        assert isinstance(value, str)
        json.loads(value)
    assert isinstance(c.get_graph_count(), int)


def test_get_graph_count_tracks_creates(graph):
    vm, c = graph
    assert c.get_graph_count() == 0
    make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    assert c.get_graph_count() == 1
    make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    assert c.get_graph_count() == 2


def test_unknown_graph_or_node_raises(graph):
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD], 10 ** 17, 100)])
    for call in (lambda: c.get_graph("ghost"),
                 lambda: c.get_node(gid, "ghost"),
                 lambda: c.get_evidence(gid, "ghost"),
                 lambda: c.settle("ghost", "n1"),
                 lambda: c.cancel("ghost"),
                 lambda: c.submit_evidence(gid, "ghost", URL_ALPHA)):
        with pytest.raises(Exception):
            call()


def test_get_node_shows_what_was_quoted(graph):
    """The audit trail: a reviewer can read the exact span that earned a node."""
    vm, c = graph
    gid = make_graph(vm, c, [node("n1", [M_BUILD, M_SIGNED], 10 ** 17, 100)])
    submit(vm, c, gid, "n1", URL_ALPHA, 10)
    remock(vm, llm=[(r".*alpha-report.*",
                     spans_json(["The build passed", "release captain"]))])
    settle(vm, c, gid, "n1", 200)
    n = node_state(vm, c, gid, "n1")
    assert n["spans"] == ["The build passed", "release captain"]
    assert n["met"] == [True, True]
    assert n["markers"] == [M_BUILD, M_SIGNED]
    assert n["sources"] == [URL_ALPHA]
