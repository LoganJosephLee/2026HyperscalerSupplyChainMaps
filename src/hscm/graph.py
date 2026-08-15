"""Build the graph from verified records, and export it.

Decision 5 keeps one node type and one edge type. Two things this module
decides that the spec left open, both flagged rather than assumed:

1. **Duplicate evidence.** The same relationship is often stated in several
   filings, and in several windows of one filing. The spec's edge model has no
   identity, so "one edge per sentence" and "one edge per company pair" are
   both readings. The export does both, for different consumers: `edges` groups
   by company pair and carries an `evidence` array (a graph canvas cannot draw
   forty parallel lines legibly), while the downloadable dataset keeps every
   evidence record flat, and the Neo4j load creates one SUPPLIES relationship
   per record. Nothing is discarded in either direction.

2. **`country` and `sector` are null** unless enriched. They are not in
   `company_tickers.json`; they come from the submissions API, one request per
   company. Empty is honest; guessing from the name is not.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config
from .resolve import Resolver

EVIDENCE_FIELDS = (
    "source_sentence",
    "source_url",
    "form_type",
    "filing_date",
    "product_or_service",
    "quantified_pct",
    "quantified_basis",
    "relationship_type",
    "extraction_confidence",
)


@dataclass
class Company:
    cik: int
    ticker: str | None
    canonical_name: str
    is_seed: bool = False
    has_sec_filings: bool = True  # everything on the spine is a filer by definition
    lei: str | None = None       # GLEIF enrichment not yet run
    country: str | None = None
    sector: str | None = None

    @property
    def node_id(self) -> str:
        return f"cik-{self.cik:010d}"


@dataclass
class SupplyEdge:
    supplier_cik: int
    buyer_cik: int
    evidence: list[dict] = field(default_factory=list)

    @property
    def edge_id(self) -> str:
        return f"cik-{self.supplier_cik:010d}--cik-{self.buyer_cik:010d}"

    @property
    def max_quantified_pct(self) -> float | None:
        values = [e["quantified_pct"] for e in self.evidence if e.get("quantified_pct") is not None]
        return max(values) if values else None

    @property
    def latest_filing_date(self) -> str:
        return max(e["filing_date"] for e in self.evidence)

    @property
    def best_confidence(self) -> str:
        order = {"high": 0, "medium": 1, "low": 2}
        return min(
            (e.get("extraction_confidence", "low") for e in self.evidence),
            key=lambda c: order.get(c, 3),
        )

    @property
    def relationship_types(self) -> list[str]:
        return sorted({e.get("relationship_type", "unclear") for e in self.evidence})


@dataclass
class Graph:
    companies: dict[int, Company] = field(default_factory=dict)
    edges: dict[tuple[int, int], SupplyEdge] = field(default_factory=dict)
    unresolved: list[dict] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)

    @property
    def data_as_of(self) -> str | None:
        """Newest filing date in the dataset — the stamp every page carries."""
        dates = [e.latest_filing_date for e in self.edges.values()]
        return max(dates) if dates else None


def build_graph(records: list[dict], resolver: Resolver, seed_ciks: set[int]) -> Graph:
    """Turn verified relationship records into companies and edges.

    Only records whose *both* parties resolve become edges. A record with one
    unresolved party is kept in `unresolved` so the coverage page can report how
    much evidence the entity resolution is currently dropping — that number is
    a property of the dataset, not an error to hide.
    """
    graph = Graph()

    for record in records:
        buyer = resolver.resolve(record.get("buyer_name_raw", ""))
        supplier = resolver.resolve(record.get("supplier_name_raw", ""))

        for resolution in (buyer, supplier):
            if resolution.status == "excluded":
                graph.excluded.setdefault(resolution.raw_name, resolution.reason)

        if not (buyer.is_resolved and supplier.is_resolved):
            graph.unresolved.append(
                {
                    "buyer_name_raw": record.get("buyer_name_raw"),
                    "supplier_name_raw": record.get("supplier_name_raw"),
                    "buyer_status": buyer.status,
                    "supplier_status": supplier.status,
                    "source_url": record.get("source_url"),
                }
            )
            continue

        if supplier.cik == buyer.cik:
            # A filing describing an intra-company arrangement resolves to one
            # node on both ends. A self-loop is not a supply relationship.
            continue

        for resolution in (buyer, supplier):
            graph.companies.setdefault(
                resolution.cik,
                Company(
                    cik=resolution.cik,
                    ticker=resolution.ticker,
                    canonical_name=resolution.canonical_name or resolution.raw_name,
                    is_seed=resolution.cik in seed_ciks,
                ),
            )

        key = (supplier.cik, buyer.cik)
        edge = graph.edges.setdefault(key, SupplyEdge(supplier.cik, buyer.cik))
        evidence = {k: record.get(k) for k in EVIDENCE_FIELDS}
        if evidence not in edge.evidence:
            edge.evidence.append(evidence)

    return graph


# --- export -----------------------------------------------------------------
def export(graph: Graph, records: list[dict], directory: Path | None = None) -> list[Path]:
    """Write the artifacts the website and the dataset downloads consume."""
    directory = directory or config.EXPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    graph_payload = {
        "meta": {
            "data_as_of": graph.data_as_of,
            "node_count": len(graph.companies),
            "edge_count": len(graph.edges),
            "evidence_count": sum(len(e.evidence) for e in graph.edges.values()),
            "unresolved_record_count": len(graph.unresolved),
            "excluded_companies": graph.excluded,
            "license": "Source filings are US government works in the public domain.",
        },
        "nodes": [
            {**asdict(company), "id": company.node_id}
            for company in sorted(graph.companies.values(), key=lambda c: c.canonical_name)
        ],
        "edges": [
            {
                "id": edge.edge_id,
                "source": f"cik-{edge.supplier_cik:010d}",
                "target": f"cik-{edge.buyer_cik:010d}",
                "quantified_pct": edge.max_quantified_pct,
                "relationship_types": edge.relationship_types,
                "confidence": edge.best_confidence,
                "latest_filing_date": edge.latest_filing_date,
                "evidence": edge.evidence,
            }
            for edge in sorted(graph.edges.values(), key=lambda e: e.edge_id)
        ],
    }

    graph_path = directory / "graph.json"
    graph_path.write_text(json.dumps(graph_payload, indent=2) + "\n")
    written.append(graph_path)

    records_path = directory / "relationships.json"
    records_path.write_text(json.dumps(records, indent=2) + "\n")
    written.append(records_path)

    csv_path = directory / "relationships.csv"
    columns = [
        "buyer_name_raw", "supplier_name_raw", "relationship_type", "product_or_service",
        "quantified_pct", "quantified_basis", "source_sentence", "source_url",
        "form_type", "filing_date", "extraction_confidence",
    ]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    written.append(csv_path)

    return written


# --- Cypher load (M6) -------------------------------------------------------
def cypher_statements(graph: Graph) -> list[tuple[str, dict]]:
    """Parameterised Cypher for loading the graph into Neo4j.

    One SUPPLIES relationship per evidence record — the faithful reading of
    Decision 5. Neo4j handles parallel relationships fine, and questions like
    "which dependencies are corroborated by more than one filing" need them
    separate.
    """
    statements: list[tuple[str, dict]] = [
        ("CREATE CONSTRAINT company_cik IF NOT EXISTS "
         "FOR (c:Company) REQUIRE c.cik IS UNIQUE", {}),
    ]

    for company in graph.companies.values():
        statements.append(
            (
                "MERGE (c:Company {cik: $cik}) "
                "SET c.ticker = $ticker, c.canonical_name = $canonical_name, "
                "c.is_seed = $is_seed, c.has_sec_filings = $has_sec_filings, "
                "c.lei = $lei, c.country = $country, c.sector = $sector",
                asdict(company),
            )
        )

    for edge in graph.edges.values():
        for evidence in edge.evidence:
            statements.append(
                (
                    "MATCH (s:Company {cik: $supplier_cik}), (b:Company {cik: $buyer_cik}) "
                    "MERGE (s)-[r:SUPPLIES {source_url: $source_url, "
                    "source_sentence: $source_sentence}]->(b) "
                    "SET r.form_type = $form_type, r.filing_date = date($filing_date), "
                    "r.product_or_service = $product_or_service, "
                    "r.quantified_pct = $quantified_pct, r.quantified_basis = $quantified_basis, "
                    "r.relationship_type = $relationship_type, "
                    "r.extraction_confidence = $extraction_confidence",
                    {
                        "supplier_cik": edge.supplier_cik,
                        "buyer_cik": edge.buyer_cik,
                        **evidence,
                    },
                )
            )

    return statements
