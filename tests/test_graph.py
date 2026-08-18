"""Tests for graph assembly and export."""

from __future__ import annotations

import csv
import json

import pytest

from hscm.graph import build_graph, cypher_statements, export
from hscm.resolve import Aliases, Resolver, Spine
from test_resolve import SPINE_PAYLOAD

MSFT, NVDA, MU = 789019, 1045810, 723125
MSFT_K, NVDA_K, MU_K = "cik-0000789019", "cik-0001045810", "cik-0000723125"


@pytest.fixture
def resolver() -> Resolver:
    return Resolver(Spine.from_json(SPINE_PAYLOAD), Aliases())


def record(supplier="NVIDIA Corporation", buyer="Microsoft Corporation", sentence=None, **extra):
    base = {
        "supplier_name_raw": supplier,
        "buyer_name_raw": buyer,
        "source_sentence": sentence or "The registrant purchases processors from a named vendor.",
        "source_url": "https://www.sec.gov/Archives/edgar/data/789019/000/x.htm",
        "form_type": "10-K",
        "filing_date": "2025-07-31",
        "relationship_type": "supplies",
        "extraction_confidence": "high",
        "quantified_pct": None,
        "quantified_basis": None,
        "product_or_service": None,
    }
    base.update(extra)
    return base


# --- assembly ---------------------------------------------------------------
def test_edge_built_between_resolved_companies(resolver):
    graph = build_graph([record()], resolver, seed_ciks={MSFT})
    assert set(graph.companies) == {MSFT_K, NVDA_K}
    assert (NVDA_K, MSFT_K) in graph.edges
    assert graph.companies[MSFT_K].is_seed
    assert not graph.companies[NVDA_K].is_seed


def test_repeated_statements_group_onto_one_edge(resolver):
    """Two filings stating the same relationship is corroboration, not two edges."""
    records = [
        record(sentence="First filing states the relationship in these words."),
        record(sentence="A later filing states it again in different words.", filing_date="2025-11-01"),
    ]
    graph = build_graph(records, resolver, seed_ciks={MSFT})
    assert len(graph.edges) == 1
    edge = graph.edges[(NVDA_K, MSFT_K)]
    assert len(edge.evidence) == 2
    assert edge.latest_filing_date == "2025-11-01"


def test_identical_records_are_not_double_counted(resolver):
    """The same window extracted twice must not inflate the evidence count."""
    graph = build_graph([record(), record()], resolver, seed_ciks=set())
    assert len(graph.edges[(NVDA_K, MSFT_K)].evidence) == 1


def test_one_sentence_read_twice_is_still_one_statement(resolver):
    """The concentration sweep re-reads text that is also inside Item 1.

    The model does not word product_or_service identically on the second pass, so
    the two records differ as dicts while citing one sentence. The site counts
    statements to show corroboration; counting this twice would claim two
    independent sources for one.
    """
    graph = build_graph(
        [
            record(product_or_service="packaging for IC products"),
            record(product_or_service="packaging of IC products"),
        ],
        resolver,
        seed_ciks=set(),
    )
    assert len(graph.edges[(NVDA_K, MSFT_K)].evidence) == 1


def test_the_same_sentence_in_two_different_filings_is_two_statements(resolver):
    graph = build_graph(
        [
            record(),
            record(source_url="https://www.sec.gov/Archives/edgar/data/1045810/000/y.htm"),
        ],
        resolver,
        seed_ciks=set(),
    )
    assert len(graph.edges[(NVDA_K, MSFT_K)].evidence) == 2


def test_unresolved_names_are_counted_not_dropped_silently(resolver):
    graph = build_graph([record(supplier="Unknown Private Vendor")], resolver, seed_ciks=set())
    assert graph.edges == {}
    assert len(graph.unresolved) == 1
    assert graph.unresolved[0]["supplier_status"] == "review"


def test_excluded_company_is_recorded_for_the_limitations_page():
    aliases = Aliases(excluded={"Samsung Electronics": "Korean-listed; not an SEC filer"})
    resolver = Resolver(Spine.from_json(SPINE_PAYLOAD), aliases)
    graph = build_graph([record(supplier="Samsung Electronics")], resolver, seed_ciks=set())
    assert graph.edges == {}
    assert graph.excluded["Samsung Electronics"] == "Korean-listed; not an SEC filer"


def test_self_loop_is_not_an_edge(resolver):
    graph = build_graph(
        [record(supplier="Microsoft Corporation", buyer="Microsoft Corp")], resolver, seed_ciks=set()
    )
    assert graph.edges == {}


def test_direction_is_supplier_to_buyer(resolver):
    graph = build_graph([record()], resolver, seed_ciks=set())
    edge = next(iter(graph.edges.values()))
    assert (edge.supplier_key, edge.buyer_key) == (NVDA_K, MSFT_K)


def test_quantified_pct_and_confidence_summarise_the_evidence(resolver):
    records = [
        record(quantified_pct=19, quantified_basis="revenue", extraction_confidence="medium"),
        record(sentence="Another sentence entirely, with a larger number.",
               quantified_pct=24, quantified_basis="revenue", extraction_confidence="high"),
    ]
    edge = build_graph(records, resolver, seed_ciks=set()).edges[(NVDA_K, MSFT_K)]
    assert edge.max_quantified_pct == 24
    assert edge.best_confidence == "high"


def test_data_as_of_is_the_newest_filing(resolver):
    records = [record(), record(sentence="Older statement.", filing_date="2024-01-15")]
    assert build_graph(records, resolver, seed_ciks=set()).data_as_of == "2025-07-31"


def test_empty_dataset_has_no_stamp(resolver):
    assert build_graph([], resolver, seed_ciks=set()).data_as_of is None


# --- export -----------------------------------------------------------------
def test_export_writes_graph_and_both_dataset_formats(tmp_path, resolver):
    records = [record(quantified_pct=19, quantified_basis="revenue")]
    graph = build_graph(records, resolver, seed_ciks={MSFT})
    written = export(graph, records, tmp_path)

    assert {p.name for p in written} == {"graph.json", "relationships.json", "relationships.csv"}

    payload = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    assert payload["meta"]["data_as_of"] == "2025-07-31"
    assert payload["meta"]["edge_count"] == 1
    assert len(payload["nodes"]) == 2
    edge = payload["edges"][0]
    assert edge["source"] == NVDA_K and edge["target"] == MSFT_K
    assert edge["quantified_pct"] == 19
    assert edge["evidence"][0]["source_url"].startswith("https://www.sec.gov/")

    rows = list(csv.DictReader((tmp_path / "relationships.csv").open(encoding="utf-8")))
    assert rows[0]["source_sentence"] == records[0]["source_sentence"]
    assert rows[0]["supplier_name_raw"] == "NVIDIA Corporation"


def test_a_filing_character_outside_latin1_survives_export(tmp_path, resolver):
    """NVIDIA's 10-K writes "non-exclusive" with U+2010, a non-breaking hyphen.

    Every export here writes text a filing supplied, and filings are full of
    typographic characters. Writing them with the platform's preferred encoding
    works on Linux and raises UnicodeEncodeError on Windows, which is where this
    was found. The whole suite runs with EncodingWarning as an error to keep any
    new file operation from reintroducing it; this checks the round trip too.
    """
    sentence = (
        "In December 2025, we entered into a non\u2010exclusive license agreement "
        "with Groq, Inc. for its language processing unit technology."
    )
    graph = build_graph([record(sentence=sentence)], resolver, seed_ciks=set())
    export(graph, [record(sentence=sentence)], tmp_path)

    payload = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    assert "\u2010" in payload["edges"][0]["evidence"][0]["source_sentence"]
    rows = list(csv.DictReader((tmp_path / "relationships.csv").open(encoding="utf-8")))
    assert "\u2010" in rows[0]["source_sentence"]


def test_every_exported_edge_carries_at_least_one_citation(tmp_path, resolver):
    graph = build_graph([record(), record(supplier="Micron Technology, Inc.")], resolver, set())
    export(graph, [], tmp_path)
    payload = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    for edge in payload["edges"]:
        assert edge["evidence"]
        for item in edge["evidence"]:
            assert item["source_sentence"] and item["source_url"]


def test_empty_graph_exports_cleanly(tmp_path, resolver):
    """An empty dataset must produce a valid file, not a crash or a stub node."""
    export(build_graph([], resolver, set()), [], tmp_path)
    payload = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    assert payload["nodes"] == [] and payload["edges"] == []
    assert payload["meta"]["data_as_of"] is None


# --- Cypher -----------------------------------------------------------------
def test_cypher_has_one_relationship_per_evidence_record(resolver):
    records = [record(), record(sentence="A second, different sentence from another filing.")]
    statements = cypher_statements(build_graph(records, resolver, seed_ciks={MSFT}))
    supplies = [s for s, _ in statements if "SUPPLIES" in s]
    assert len(supplies) == 2  # grouped in the export, separate in the workbench


def test_cypher_is_parameterised_not_interpolated(resolver):
    """Filing sentences contain quotes and backslashes; string-built Cypher would break."""
    graph = build_graph([record(sentence='He said "we depend on a single vendor" in the filing.')],
                        resolver, seed_ciks=set())
    statement, params = next((s, p) for s, p in cypher_statements(graph) if "SUPPLIES" in s)
    assert '"' not in statement
    assert params["source_sentence"].startswith('He said "we depend')


def test_cypher_creates_the_uniqueness_constraint_first(resolver):
    statements = cypher_statements(build_graph([record()], resolver, set()))
    assert "CREATE CONSTRAINT" in statements[0][0]


# --- direction ---------------------------------------------------------------
def test_unclear_only_edge_reports_no_stated_direction(resolver):
    """"A strategic partnership with X" says nothing about which way goods flow."""
    graph = build_graph(
        [record(relationship_type="unclear",
                sentence="Additionally, we have a long-term strategic partnership with them.")],
        resolver, seed_ciks=set(),
    )
    assert graph.edges[(NVDA_K, MSFT_K)].direction_stated is False


def test_one_directional_statement_is_enough(resolver):
    records = [
        record(relationship_type="unclear"),
        record(relationship_type="supplies", sentence="They supply us with processors."),
    ]
    assert build_graph(records, resolver, seed_ciks=set()).edges[(NVDA_K, MSFT_K)].direction_stated


def test_export_carries_direction_stated(tmp_path, resolver):
    graph = build_graph([record(relationship_type="unclear")], resolver, seed_ciks=set())
    export(graph, [], tmp_path)
    payload = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    assert payload["edges"][0]["direction_stated"] is False


# --- non-filer nodes ---------------------------------------------------------
def test_non_filer_becomes_a_node_with_no_cik():
    """Microsoft's 10-K names OpenAI. It is citable, so it is in the graph."""
    aliases = Aliases(non_filers={"OpenAI": "Private; named in filings, files none"})
    resolver = Resolver(Spine.from_json(SPINE_PAYLOAD), aliases)
    graph = build_graph(
        [record(supplier="OpenAI", buyer="Microsoft Corporation", relationship_type="unclear")],
        resolver, seed_ciks={MSFT},
    )
    node = graph.companies["name-openai"]
    assert node.cik is None
    assert node.has_sec_filings is False
    assert node.is_seed is False
    assert ("name-openai", MSFT_K) in graph.edges


def test_export_counts_non_filer_nodes(tmp_path):
    aliases = Aliases(non_filers={"OpenAI": "Private"})
    resolver = Resolver(Spine.from_json(SPINE_PAYLOAD), aliases)
    graph = build_graph([record(supplier="OpenAI")], resolver, seed_ciks=set())
    export(graph, [], tmp_path)
    payload = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    assert payload["meta"]["non_filer_node_count"] == 1
    node = next(n for n in payload["nodes"] if n["id"] == "name-openai")
    assert node["cik"] is None and node["has_sec_filings"] is False


def test_cypher_keys_on_node_key_so_null_ciks_do_not_collide():
    aliases = Aliases(non_filers={"OpenAI": "x", "xAI": "y"})
    resolver = Resolver(Spine.from_json(SPINE_PAYLOAD), aliases)
    graph = build_graph(
        [record(supplier="OpenAI"), record(supplier="xAI", sentence="A second sentence here.")],
        resolver, seed_ciks=set(),
    )
    statements = cypher_statements(graph)
    assert "node_key IS UNIQUE" in statements[0][0]
    keys = {p["node_key"] for s, p in statements if "MERGE (c:Company" in s}
    assert {"name-openai", "name-xai"} <= keys
