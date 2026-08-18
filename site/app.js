/*
 * The graph canvas and, more importantly, the edge -> citation interaction.
 *
 * The citation panel is the point of this project: an edge the reader cannot
 * trace back to a sentence in a filing is worth nothing here. So the click
 * handler renders every piece of evidence behind an edge, verbatim, with the
 * form type, the filing date, and a direct link to EDGAR.
 */

const SEED_COLOR = "#f2a541";
const SUPPLIER_COLOR = "#6aa9ff";
const NON_FILER_COLOR = "#c39bd3";
const EDGE_COLOR = "#3b4252";
const EDGE_COLOR_QUANTIFIED = "#7ee0a0";

const el = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

/* Edge width encodes the disclosed percentage where a filing states one.
   Everything else renders thin, so "we don't know" never looks like "small". */
function edgeSize(pct) {
  if (pct === null || pct === undefined) return 1;
  return 1.5 + Math.sqrt(pct) * 1.1;
}

function renderEvidence(edge, nodesById) {
  const supplier = nodesById.get(edge.source);
  const buyer = nodesById.get(edge.target);

  const undirected = edge.direction_stated === false;
  const header = `
    <p class="pair">
      ${escapeHtml(supplier?.canonical_name ?? edge.source)}
      <span class="arrow">${undirected ? "&harr;" : "supplies"}</span>
      ${escapeHtml(buyer?.canonical_name ?? edge.target)}
    </p>
    ${undirected ? `<p class="pair-sub" style="color:var(--warn)">
      No filing states which way goods or services flow. Shown without a
      direction; the order of the two names carries no meaning.
    </p>` : ""}
    <p class="pair-sub">
      ${edge.evidence.length} statement${edge.evidence.length === 1 ? "" : "s"} across
      ${new Set(edge.evidence.map((e) => e.source_url)).size} filing${
        new Set(edge.evidence.map((e) => e.source_url)).size === 1 ? "" : "s"
      }
    </p>`;

  const blocks = edge.evidence.map((item) => {
    const tags = [
      `<span class="tag">${escapeHtml(item.form_type)}</span>`,
      `<span class="tag">filed ${escapeHtml(item.filing_date)}</span>`,
      `<span class="tag${item.relationship_type === "unclear" ? " unclear" : ""}">${escapeHtml(
        item.relationship_type
      )}</span>`,
      `<span class="tag">confidence: ${escapeHtml(item.extraction_confidence)}</span>`,
    ];
    if (item.quantified_pct !== null && item.quantified_pct !== undefined) {
      tags.splice(2, 0, `<span class="tag pct">${escapeHtml(item.quantified_pct)}% of ${escapeHtml(
        item.quantified_basis ?? "?"
      )}</span>`);
    }
    if (item.product_or_service) {
      tags.push(`<span class="tag">${escapeHtml(item.product_or_service)}</span>`);
    }

    return `
      <div class="evidence">
        <blockquote>${escapeHtml(item.source_sentence)}</blockquote>
        <div class="meta">${tags.join("")}</div>
        <a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener">
          Read this filing on EDGAR &rarr;
        </a>
      </div>`;
  });

  el("panel-title").textContent = "Evidence";
  el("panel").innerHTML = header + blocks.join("");
}

function renderNode(node, graphData) {
  const asSupplier = graphData.edges.filter((e) => e.source === node.id);
  const asBuyer = graphData.edges.filter((e) => e.target === node.id);
  const nodesById = new Map(graphData.nodes.map((n) => [n.id, n]));
  const list = (edges, key) =>
    edges.length
      ? `<ul class="node-list">${edges
          .map((e) => {
            const other = nodesById.get(e[key]);
            return `<li>${escapeHtml(other?.canonical_name ?? e[key])}
              <span class="tk">${escapeHtml(other?.ticker ?? "")}</span></li>`;
          })
          .join("")}</ul>`
      : `<p class="hint">None disclosed.</p>`;

  el("panel-title").textContent = "Company";
  el("panel").innerHTML = `
    <p class="pair">${escapeHtml(node.canonical_name)}</p>
    <p class="pair-sub">
      ${node.cik == null
        ? "No SEC filings &mdash; in this graph only because other companies' filings name it"
        : "CIK " + escapeHtml(String(node.cik).padStart(10, "0"))}${
        node.ticker ? " &middot; " + escapeHtml(node.ticker) : ""}
      ${node.is_seed ? " &middot; seed company" : ""}
    </p>
    <h2>Supplies</h2>${list(asSupplier, "target")}
    <h2 style="margin-top:20px">Buys from</h2>${list(asBuyer, "source")}`;
}

async function main() {
  let data;
  try {
    const response = await fetch("data/graph.json", { cache: "no-store" });
    data = await response.json();
  } catch (error) {
    el("empty-state").classList.add("visible");
    el("empty-state").querySelector("strong").textContent = "Could not load data/graph.json.";
    return;
  }

  const stamp = data.meta?.data_as_of;
  el("stamp").textContent = stamp ? `data as of ${stamp}` : "data as of — (no filings yet)";
  document.title = stamp
    ? `AI Hyperscaler Supply Chain Maps — ${stamp}`
    : document.title;

  if (!data.edges?.length) {
    el("empty-state").classList.add("visible");
    return;
  }

  const graph = new graphology.Graph({ type: "directed" });
  const nodesById = new Map(data.nodes.map((n) => [n.id, n]));

  data.nodes.forEach((node, index) => {
    const angle = (2 * Math.PI * index) / data.nodes.length;
    graph.addNode(node.id, {
      // Seeded on a circle so ForceAtlas2 has a deterministic starting point;
      // a random layout makes the same dataset look different on every load.
      x: Math.cos(angle) * 10,
      y: Math.sin(angle) * 10,
      size: node.is_seed ? 14 : 8,
      label: node.canonical_name,
      // A company named in a filing but filing nothing itself gets its own
      // colour: it is in the graph on someone else's disclosure, and nothing
      // it says can ever corroborate or contradict the edge.
      color: node.is_seed
        ? SEED_COLOR
        : node.has_sec_filings === false
          ? NON_FILER_COLOR
          : SUPPLIER_COLOR,
    });
  });

  data.edges.forEach((edge) => {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) return;
    graph.addEdgeWithKey(edge.id, edge.source, edge.target, {
      size: edgeSize(edge.quantified_pct),
      color: edge.quantified_pct == null ? EDGE_COLOR : EDGE_COLOR_QUANTIFIED,
      // No arrowhead when no filing stated which way anything flows. The stored
      // supplier/buyer order is a property of the record shape, not evidence.
      type: edge.direction_stated === false ? "line" : "arrow",
    });
  });

  forceAtlas2.assign(graph, {
    iterations: 300,
    settings: { ...forceAtlas2.inferSettings(graph), gravity: 1.4, scalingRatio: 12 },
  });

  const renderer = new Sigma(graph, el("graph"), {
    enableEdgeEvents: true, // sigma v3 does not emit edge clicks without this
    renderEdgeLabels: false,
    labelColor: { color: "#e6e8ec" },
    labelSize: 12,
    defaultEdgeType: "arrow",
    minCameraRatio: 0.1,
    maxCameraRatio: 6,
  });

  renderer.on("clickEdge", ({ edge }) => {
    const found = data.edges.find((e) => e.id === edge);
    if (found) renderEvidence(found, nodesById);
  });

  renderer.on("clickNode", ({ node }) => {
    const found = nodesById.get(node);
    if (found) renderNode(found, data);
  });

  window.__graph = { graph, renderer, data }; // handle for tests and console work
}

main();
