// Queries for the Neo4j workbench (M6).
//
// The graph is one node label and one relationship type:
//   (:Company {cik, ticker, canonical_name, is_seed, has_sec_filings, lei, country, sector})
//   -[:SUPPLIES {source_sentence, source_url, form_type, filing_date,
//                product_or_service, quantified_pct, quantified_basis,
//                relationship_type, extraction_confidence}]->
//
// Direction is supplier -> buyer. One relationship per evidence sentence, so a
// pair of companies can be joined by several SUPPLIES relationships; count
// DISTINCT endpoints when you mean "how many companies", not "how much evidence".
//
// Load with:  uv run hscm neo4j-load --uri bolt://localhost:7687 --password <pw>
// Or print the statements without a database:  uv run hscm neo4j-load --dry-run


// --- Most-connected suppliers ------------------------------------------------
// Which companies the most distinct buyers depend on. DISTINCT matters: a
// supplier named in six sentences of one 10-K is not six customers.
MATCH (s:Company)-[:SUPPLIES]->(b:Company)
RETURN s.canonical_name AS supplier,
       s.ticker         AS ticker,
       count(DISTINCT b) AS buyers,
       collect(DISTINCT b.ticker) AS buyer_tickers
ORDER BY buyers DESC, supplier
LIMIT 25;


// --- Single-source dependencies ----------------------------------------------
// Buyers with exactly one disclosed supplier for a given product or service.
// Read this as "one disclosed supplier", never "one supplier" — the filing-only
// dataset cannot see undisclosed alternatives.
MATCH (s:Company)-[r:SUPPLIES]->(b:Company)
WHERE r.product_or_service IS NOT NULL
WITH b, r.product_or_service AS product, collect(DISTINCT s.canonical_name) AS suppliers
WHERE size(suppliers) = 1
RETURN b.canonical_name AS buyer, product, suppliers[0] AS sole_disclosed_supplier
ORDER BY buyer, product;


// --- Shortest path between two hyperscalers ----------------------------------
// Undirected: we are asking whether two buyers share exposure, and a shared
// supplier links them through an edge that points at both of them.
MATCH (a:Company {ticker: 'MSFT'}), (b:Company {ticker: 'AMZN'})
MATCH path = shortestPath((a)-[:SUPPLIES*..6]-(b))
RETURN [n IN nodes(path) | n.canonical_name] AS hops, length(path) AS hop_count;


// --- Shared suppliers between two buyers -------------------------------------
// The more useful form of the question above: not the path, but the overlap.
MATCH (a:Company {ticker: 'MSFT'})<-[:SUPPLIES]-(s:Company)-[:SUPPLIES]->(b:Company {ticker: 'AMZN'})
RETURN DISTINCT s.canonical_name AS shared_supplier, s.ticker AS ticker
ORDER BY shared_supplier;


// --- Concentration risk: suppliers every seed company depends on --------------
MATCH (seed:Company {is_seed: true})
WITH count(DISTINCT seed) AS seed_count
MATCH (s:Company)-[:SUPPLIES]->(b:Company {is_seed: true})
WITH s, seed_count, count(DISTINCT b) AS seeds_served
WHERE seeds_served = seed_count
RETURN s.canonical_name AS supplier, seeds_served
ORDER BY supplier;


// --- Degree centrality -------------------------------------------------------
// Total distinct trading partners in either direction.
MATCH (c:Company)
OPTIONAL MATCH (c)-[:SUPPLIES]->(buyer:Company)
OPTIONAL MATCH (supplier:Company)-[:SUPPLIES]->(c)
RETURN c.canonical_name AS company,
       count(DISTINCT buyer)    AS sells_to,
       count(DISTINCT supplier) AS buys_from,
       count(DISTINCT buyer) + count(DISTINCT supplier) AS degree
ORDER BY degree DESC, company
LIMIT 25;


// --- Corroboration: relationships stated in more than one filing --------------
// The edges we are most confident in are the ones several filings support.
MATCH (s:Company)-[r:SUPPLIES]->(b:Company)
WITH s, b, count(r) AS evidence_count, collect(DISTINCT r.form_type) AS forms
WHERE evidence_count > 1
RETURN s.canonical_name AS supplier, b.canonical_name AS buyer, evidence_count, forms
ORDER BY evidence_count DESC;


// --- Quantified edges only ----------------------------------------------------
// Where a filing actually put a number on the relationship.
MATCH (s:Company)-[r:SUPPLIES]->(b:Company)
WHERE r.quantified_pct IS NOT NULL
RETURN s.canonical_name AS supplier, b.canonical_name AS buyer,
       r.quantified_pct AS pct, r.quantified_basis AS basis,
       r.form_type AS form, toString(r.filing_date) AS filed, r.source_url AS source
ORDER BY pct DESC;
