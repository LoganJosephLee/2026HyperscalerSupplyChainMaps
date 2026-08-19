/*
 * The graph canvas and, more importantly, the edge -> citation interaction.
 *
 * The citation panel is the point of this project: an edge the reader cannot
 * trace back to a sentence in a filing is worth nothing here. So the click
 * handler renders every piece of evidence behind an edge, verbatim, with the
 * form type, the filing date, and a direct link to EDGAR.
 */

// Three hues, and three is the ceiling. A network graph puts any two nodes side
// by side, so the palette has to hold up across every pair, not just neighbours
// in a legend — and no fourth hue tested clears the colour-blind and
// normal-vision separation floors under that condition. So colour answers one
// question only: can this company's word be checked? What each company *does* is
// carried by position and a label instead, which has no such ceiling.
const SEED_COLOR = "#d95926";       // a buyer: one of the hyperscalers
const SUPPLIER_COLOR = "#3987e5";   // files with the SEC; its own filings can corroborate
const NON_FILER_COLOR = "#199e70";  // files nothing; here on someone else's word
const EDGE_COLOR = "#3b4252";
// Deliberately not a hue from the node palette. Green now means "this company
// files nothing with the SEC", and a green edge next to a green node invites the
// reader to connect two things that have nothing to do with each other.
const EDGE_COLOR_QUANTIFIED = "#cfd6e4";
const DIMMED = "#2a2f3a";

// Percentages in filings do not share a denominator. The label says which one this
// number has, so a thick edge is never read as a magnitude comparable to another.
// Read aloud with the supplier as subject. A bare "supplies" tag next to two
// company names does not say which name is the subject, and that ambiguity is
// exactly where a reversed edge hides.
const TYPE_PHRASE = {
  supplies: (supplier, buyer) => `${supplier} supplies ${buyer}`,
  manufactures_for: (supplier, buyer) => `${supplier} manufactures for ${buyer}`,
  leases_capacity_to: (supplier, buyer) => `${supplier} leases capacity to ${buyer}`,
  licenses_to: (supplier, buyer) => `${supplier} licenses to ${buyer}`,
  unclear: (supplier, buyer) => `${supplier} and ${buyer} — direction not stated`,
};

const BASIS_LABEL = {
  revenue: "of revenue",
  cost: "of cost",
  units: "of units supplied",
  other: "— see the sentence for what of",
};

const el = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

/* Edge width encodes the disclosed percentage where a filing states one.
   Everything else renders thin, so "we don't know" never looks like "small". */
function edgeSize(pct) {
  // Square-rooted and tightly bounded. A 95% edge is worth noticing; at the
  // previous scaling it was a twelve-pixel beam across the canvas that hid every
  // undisclosed relationship behind it, which inverts what the map is for — the
  // quantified ones are the rare, easy case.
  if (pct === null || pct === undefined) return 0.8;
  return 1.2 + Math.sqrt(pct) * 0.45;
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
        (TYPE_PHRASE[item.relationship_type] ??
          ((s2, b2) => `${s2} — ${item.relationship_type} — ${b2}`))(
          supplier?.canonical_name ?? edge.source,
          buyer?.canonical_name ?? edge.target
        )
      )}</span>`,
      `<span class="tag">confidence: ${escapeHtml(item.extraction_confidence)}</span>`,
    ];
    if (item.quantified_pct !== null && item.quantified_pct !== undefined) {
      tags.splice(2, 0, `<span class="tag pct">${escapeHtml(item.quantified_pct)}% ${escapeHtml(
        BASIS_LABEL[item.quantified_basis] ?? "— see the sentence for what of"
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


// What this company does, in words, plus the filing phrases that decided it. The
// phrases matter as much as the category: they are the evidence, and they let a
// reader disagree with the grouping rather than take it on faith.
function renderJob(node, graphData) {
  const meta = graphData.meta || {};
  const key = node.function || "unstated";
  const label = (meta.function_labels || {})[key];
  const description = (meta.function_descriptions || {})[key];
  if (!label) return "";

  const phrases = (node.function_phrases || []).slice(0, 6);
  return `
    <div class="job">
      <p class="job-label">${escapeHtml(label)}</p>
      ${description ? `<p class="job-desc">${escapeHtml(description)}</p>` : ""}
      ${phrases.length
        ? `<p class="job-src">Filings describe it as:
             ${phrases.map((p) => `<span class="tag">${escapeHtml(p)}</span>`).join(" ")}</p>`
        : ""}
    </div>`;
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
    ${renderJob(node, graphData)}
    <h2>Supplies</h2>${list(asSupplier, "target")}
    <h2 style="margin-top:20px">Buys from</h2>${list(asBuyer, "source")}`;
}


// --- what each cluster is -----------------------------------------------------
// A heading floating over each group. Without it the clustering is just a
// pleasing shape; with it, position is a labelled category and colour is free to
// mean something else entirely.
function legendBounds() {
  const legend = document.querySelector("#graph .legend");
  const host = el("graph");
  if (!legend || !host) return null;
  const a = legend.getBoundingClientRect();
  const b = host.getBoundingClientRect();
  return { left: a.left - b.left, right: a.right - b.left, top: a.top - b.top, bottom: a.bottom - b.top };
}

function buildClusterLabels(renderer, graph, present, labels, anchors) {
  const layer = document.createElement("div");
  layer.className = "cluster-layer";
  el("graph").appendChild(layer);

  const headings = new Map();
  present.forEach((key) => {
    const heading = document.createElement("div");
    heading.className = "cluster-label";
    heading.dataset.function = key;
    heading.textContent = labels[key] || key;
    layer.appendChild(heading);
    headings.set(key, heading);
  });

  const place = () => {
    // Measured in viewport pixels, not graph units. Sigma's y axis points the
    // other way from the screen's, so "the top of the group" computed in graph
    // space put every heading underneath its own cluster, on top of the company
    // names it was supposed to sit clear of.
    const boxes = new Map();
    graph.forEachNode((_id, attributes) => {
      const point = renderer.graphToViewport(attributes);
      const box = boxes.get(attributes.hscmFunction) || {
        sumX: 0, minY: Infinity, n: 0,
      };
      box.sumX += point.x;
      box.minY = Math.min(box.minY, point.y);
      box.n += 1;
      boxes.set(attributes.hscmFunction, box);
    });

    const blocked = legendBounds();
    headings.forEach((heading, key) => {
      const box = boxes.get(key);
      if (!box) return;
      const x = box.sumX / box.n;
      const y = box.minY - 22;
      heading.style.transform = `translate(-50%, -100%) translate(${x}px, ${y}px)`;
      // A heading printed over the legend is unreadable and makes the legend
      // unreadable too. Better to drop it: the cluster is still labelled in the
      // legend itself, and every node keeps its own name.
      const hidden =
        y < 18 ||
        (blocked &&
          x > blocked.left - 90 && x < blocked.right + 90 &&
          y > blocked.top - 30 && y < blocked.bottom + 30);
      heading.style.opacity = hidden ? "0" : "1";
    });
  };

  renderer.on("afterRender", place);
  place();
}

// --- legend -------------------------------------------------------------------
function buildLegend(present, labels, graph, renderer, descriptions) {
  const list = el("function-legend");
  if (!list) return;

  present.forEach((key) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "row function-row";
    row.dataset.function = key;
    // The plain-English line is the point of the whole grouping. Someone who
    // already knows what a foundry is did not need this map.
    row.title = descriptions?.[key] || "";
    row.innerHTML = `<span class="pin"></span> ${escapeHtml(labels[key] || key)}`;
    list.appendChild(row);
  });

  const row_description = (key) => descriptions?.[key] || "";
  let isolated = null;
  const apply = () => {
    graph.forEachNode((id, attributes) => {
      const dim = isolated !== null && attributes.hscmFunction !== isolated;
      graph.setNodeAttribute(id, "highlighted", false);
      graph.setNodeAttribute(id, "hidden", false);
      graph.setNodeAttribute(id, "color", dim ? DIMMED : attributes.hscmBaseColor);
    });
    list.querySelectorAll(".function-row").forEach((row) => {
      row.classList.toggle("active", row.dataset.function === isolated);
    });
    const note = el("function-note");
    if (note) {
      note.textContent = isolated ? row_description(isolated) : "";
      note.hidden = !isolated;
    }
    renderer.refresh();
  };

  graph.forEachNode((id, attributes) => {
    graph.setNodeAttribute(id, "hscmBaseColor", attributes.color);
  });

  list.querySelectorAll(".function-row").forEach((row) => {
    row.addEventListener("click", () => {
      isolated = isolated === row.dataset.function ? null : row.dataset.function;
      apply();
    });
  });
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
  // Keep whatever the page is called and append the stamp, so the name lives in
  // one place (the <title> tag) instead of being retyped here.
  if (stamp) document.title = `${document.title} — ${stamp}`;

  if (!data.edges?.length) {
    el("empty-state").classList.add("visible");
    return;
  }

  const graph = new graphology.Graph({ type: "directed" });
  const nodesById = new Map(data.nodes.map((n) => [n.id, n]));

  // Group companies by the job their own filings describe. Position is what
  // carries this: it has no discriminability ceiling the way colour does, and a
  // labelled cluster answers "what is this company for?" at a glance, which is
  // the question the map exists to answer.
  const labels = data.meta?.function_labels || {};
  const present = Object.keys(labels).filter((key) =>
    data.nodes.some((node) => (node.function || "unstated") === key)
  );
  const anchors = new Map(
    present.map((key, index) => {
      const angle = (2 * Math.PI * index) / present.length - Math.PI / 2;
      return [key, { x: Math.cos(angle) * 100, y: Math.sin(angle) * 100 }];
    })
  );
  const functionOf = (node) => (node.function || "unstated");

  data.nodes.forEach((node, index) => {
    const anchor = anchors.get(functionOf(node)) || { x: 0, y: 0 };
    // Deterministic jitter: the same dataset must lay out the same way twice.
    const spread = 18;
    const offset = (index * 2.399963); // golden angle, so groups fan out evenly
    graph.addNode(node.id, {
      x: anchor.x + Math.cos(offset) * spread,
      y: anchor.y + Math.sin(offset) * spread,
      size: node.is_seed ? 14 : 8,
      label: node.canonical_name,
      // Colour answers one question: can this company's word be checked? A
      // company named in a filing but filing nothing itself is in the graph on
      // someone else's disclosure, and nothing it says can ever corroborate or
      // contradict the edge.
      color: node.is_seed
        ? SEED_COLOR
        : node.has_sec_filings === false
          ? NON_FILER_COLOR
          : SUPPLIER_COLOR,
      hscmFunction: functionOf(node),
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
    iterations: 220,
    settings: { ...forceAtlas2.inferSettings(graph), gravity: 1.1, scalingRatio: 14 },
  });

  // ForceAtlas2 optimises for edges, not for grouping, so it pulls suppliers in
  // around whoever buys from them and the clusters dissolve. Blending each node
  // back toward its group keeps both readings: the cluster tells you the job,
  // the remaining pull tells you who it trades with.
  const PULL = 0.55;
  graph.forEachNode((id, attributes) => {
    const anchor = anchors.get(attributes.hscmFunction);
    if (!anchor) return;
    graph.setNodeAttribute(id, "x", attributes.x * (1 - PULL) + anchor.x * PULL);
    graph.setNodeAttribute(id, "y", attributes.y * (1 - PULL) + anchor.y * PULL);
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

  // Sigma fits the nodes to the canvas; the labels hang off them and get cut at
  // the edges. Pull the camera back so names on the outermost companies are
  // readable, which for a map whose whole point is naming companies is not a
  // detail.
  renderer.getCamera().setState({ ratio: 1.5 });

  // The primer is the panel's opening state, and the first click on anything
  // replaces it for good. Someone who needed it once may well need it twice.
  const primer = el("panel").innerHTML;
  const howTo = el("how-to-read");
  if (howTo) {
    howTo.addEventListener("click", (event) => {
      event.preventDefault();
      el("panel-title").textContent = "Evidence";
      el("panel").innerHTML = primer;
    });
  }

  buildClusterLabels(renderer, graph, present, labels, anchors);
  buildLegend(present, labels, graph, renderer, data.meta?.function_descriptions);

  window.__graph = { graph, renderer, data }; // handle for tests and console work
}

main();
