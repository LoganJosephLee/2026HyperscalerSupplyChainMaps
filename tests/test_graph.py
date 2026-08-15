"""Tests for graph assembly and export."""

from __future__ import annotations

import csv
import json

import pytest

from hscm.graph import build_graph, cypher_statements, export
from hscm.resolve import Aliases, Resolver, Spine
from test_resolve import SPINE_PAYLOAD

MSFT, NVDA, MU = 789019, 1045810, 723125


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
    assert set(graph.companies) == {MSFT, NVDA}
    assert (NVDA, MSFT) in graph.edges
    assert graph.companies[MSFT].is_seed
    assert not graph.companies[NVDA].is_seed


def test_repeated_statements_group_onto_one_edge(resolver):
    """Two filings stating the same relationship is corroboration, not two edges."""
    records = [
        record(sentence="First filing states the relationship in these words."),
        record(sentence="A later filing states it again in different words.", filing_date="2025-11-01"),
    ]
    graph = build_graph(records, resolver, seed_ciks={MSFT})
    assert len(graph.edges) == 1
    edge = graph.edges[(NVDA, MSFT)]
    assert len(edge.evidence) == 2
    assert edge.latest_filing_date == "2025-11-01"


def test_identical_records_are_not_double_counted(resolver):
    """The same window extracted twice must not inflate the evidence count."""
    graph = build_graph([record(), record()], resolver, seed_ciks=set())
    assert len(graph.edges[(NVDA, MSFT)].evidence) == 1


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
    assert (edge.supplier_cik, edge.buyer_cik) == (NVDA, MSFT)


def test_quantified_pct_and_confidence_summarise_the_evidence(resolver):
    records = [
        record(quantified_pct=19, quantified_basis="revenue", extraction_confidence="medium"),
        record(sentence="Another sentence entirely, with a larger number.",
               quantified_pct=24, quantified_basis="revenue", extraction_confidence="high"),
    ]
    edge = build_graph(records, resolver, seed_ciks=set()).edges[(NVDA, MSFT)]
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

    payload = json.loads((tmp_path / "graph.json").read_text())
    assert payload["meta"]["data_as_of"] == "2025-07-31"
    assert payload["meta"]["edge_count"] == 1
    assert len(payload["nodes"]) == 2
    edge = payload["edges"][0]
    assert edge["source"] == f"cik-{NVDA:010d}" and edge["target"] == f"cik-{MSFT:010d}"
    assert edge["quantified_pct"] == 19
    assert edge["evidence"][0]["source_url"].startswith("https://www.sec.gov/")

    rows = list(csv.DictReader((tmp_path / "relationships.csv").open()))
    assert rows[0]["source_sentence"] == records[0]["source_sentence"]
    assert rows[0]["supplier_name_raw"] == "NVIDIA Corporation"


def test_every_exported_edge_carries_at_least_one_citation(tmp_path, resolver):
    graph = build_graph([record(), record(supplier="Micron Technology, Inc.")], resolver, set())
    export(graph, [], tmp_path)
    payload = json.loads((tmp_path / "graph.json").read_text())
    for edge in payload["edges"]:
        assert edge["evidence"]
        for item in edge["evidence"]:
            assert item["source_sentence"] and item["source_url"]


def test_empty_graph_exports_cleanly(tmp_path, resolver):
    """An empty dataset must produce a valid file, not a crash or a stub node."""
    export(build_graph([], resolver, set()), [], tmp_path)
    payload = json.loads((tmp_path / "graph.json").read_text())
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
