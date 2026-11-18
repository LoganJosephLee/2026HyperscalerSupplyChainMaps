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
function nodeSize(node, statements) {
  // Square-rooted: a company with twenty statements behind it is not twenty
  // times more important than one with a single sentence, and drawn that way it
  // would swallow the map.
  const base = node.is_seed ? 12 : 5;
  return base + Math.sqrt(statements) * 1.7;
}

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
  return {
    // Group headings only mean something in the cluster arrangement. Over the
    // chain they would sit above columns they do not describe.
    setVisible(visible) { layer.style.display = visible ? "" : "none"; },
  };
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

  list.querySelectorAll(".function-row").forEach((row) => {
    row.addEventListener("click", () => {
      isolated = isolated === row.dataset.function ? null : row.dataset.function;
      apply();
    });
  });
}

// --- layouts ------------------------------------------------------------------
// Three ways of standing in the same room. The graph never changes; where the
// nodes sit does, and each arrangement answers a different question.
//
//   chain     what order does the world happen in?   (tiers, left to right)
//   clusters  who does the same job as who?          (force layout, grouped)
//   focus     what does one buyer depend on?         (rings by hop distance)
//
// Each returns {id: {x, y}} in graph coordinates and touches no renderer, so
// changing view is only ever a matter of moving points.

const LAYOUT_SPAN = 260;

function chainLayout(graph, data) {
  const tiers = data.meta?.function_tiers || {};
  const lastTier = Math.max(8, ...Object.values(tiers));
  const columns = new Map();

  graph.forEachNode((id, attributes) => {
    // A buyer belongs at the end of the chain whatever its filings say it does,
    // because the chain is drawn towards the companies it was built around.
    const tier = attributes.hscmSeed ? lastTier : (tiers[attributes.hscmFunction] ?? lastTier);
    if (!columns.has(tier)) columns.set(tier, []);
    columns.get(tier).push(id);
  });

  const positions = {};
  const used = [...columns.keys()].sort((a, b) => a - b);
  used.forEach((tier, index) => {
    const members = columns.get(tier);
    // Busiest companies to the middle of the column, where the eye lands and
    // where their edges have the least distance to travel.
    members.sort((a, b) => graph.degree(b) - graph.degree(a));
    const x = -LAYOUT_SPAN + (index / Math.max(used.length - 1, 1)) * LAYOUT_SPAN * 2;
    // Company names are long and drawn horizontally, so rows need real space
    // between them or the labels collide and sigma starts hiding them — on a map
    // whose whole point is naming companies, a hidden name is a failure. Rows
    // are spaced generously and nudged sideways in alternation, which breaks up
    // the label collisions a single straight column guarantees.
    const step = Math.max(30, (LAYOUT_SPAN * 1.5) / Math.max(members.length, 1));
    // Half a row of vertical offset on alternate columns. Without it the busiest
    // company in every column sits on the same centre line, and their labels —
    // the longest ones, because the busiest companies have the longest names —
    // all collide along it.
    const stagger = index % 2 ? step / 2 : 0;
    members.forEach((id, row) => {
      const offset = Math.ceil(row / 2) * (row % 2 ? -1 : 1);
      positions[id] = { x: x + (row % 2 ? 16 : -16), y: offset * step + stagger };
    });
  });
  return positions;
}

// Computed once, from ForceAtlas2, then remembered. The force layout is what
// carries "these two trade with each other", it is not cheap, and recomputing
// it would move companies that had no reason to move.
function computeClusterPositions(graph, anchors) {
  const counts = new Map();
  graph.forEachNode((id, attributes) => {
    const key = attributes.hscmFunction;
    const anchor = anchors.get(key) || { x: 0, y: 0 };
    const index = counts.get(key) || 0;
    counts.set(key, index + 1);
    // Phyllotactic spiral for the starting point: even spacing, nothing
    // stacked, and the same dataset lands the same way every time.
    const radius = 9 * Math.sqrt(index);
    const angle = index * 2.399963;
    graph.setNodeAttribute(id, "x", anchor.x + Math.cos(angle) * radius);
    graph.setNodeAttribute(id, "y", anchor.y + Math.sin(angle) * radius);
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
  const positions = {};
  graph.forEachNode((id, attributes) => {
    const anchor = anchors.get(attributes.hscmFunction) || { x: 0, y: 0 };
    positions[id] = {
      x: attributes.x * (1 - PULL) + anchor.x * PULL,
      y: attributes.y * (1 - PULL) + anchor.y * PULL,
    };
  });
  return positions;
}

function focusLayout(graph, rootId) {
  // Breadth-first from one company, ignoring edge direction: the question is
  // "how far from this company is that one", and a supplier's supplier is two
  // steps away whichever way the goods flow.
  const depth = new Map([[rootId, 0]]);
  let frontier = [rootId];
  while (frontier.length) {
    const next = [];
    frontier.forEach((id) => {
      graph.forEachNeighbor(id, (other) => {
        if (!depth.has(other)) {
          depth.set(other, depth.get(id) + 1);
          next.push(other);
        }
      });
    });
    frontier = next;
  }

  const rings = new Map();
  graph.forEachNode((id) => {
    // Anything unreachable gets an outer ring of its own rather than being
    // hidden. Not connected to this buyer is a finding, not an absence.
    const ring = depth.has(id) ? depth.get(id) : Infinity;
    if (!rings.has(ring)) rings.set(ring, []);
    rings.get(ring).push(id);
  });

  const positions = {};
  const finite = [...rings.keys()].filter(Number.isFinite).sort((a, b) => a - b);
  const order = [...finite, ...(rings.has(Infinity) ? [Infinity] : [])];
  order.forEach((ring, index) => {
    const members = rings.get(ring);
    const radius = ring === 0 ? 0 : (index / Math.max(order.length - 1, 1)) * LAYOUT_SPAN;
    members.sort((a, b) => graph.degree(b) - graph.degree(a));
    members.forEach((id, position) => {
      const angle = (2 * Math.PI * position) / members.length - Math.PI / 2;
      positions[id] = { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
    });
  });
  return positions;
}

// --- movement -----------------------------------------------------------------
// Nodes travel to their new places rather than teleporting. Watching a company
// move from one arrangement to another is how a reader learns that both
// pictures are the same graph; a cut makes them look like two different maps.
function glideTo(graph, positions, done) {
  const from = new Map();
  graph.forEachNode((id, attributes) => from.set(id, { x: attributes.x, y: attributes.y }));

  const start = performance.now();
  const DURATION = 750;
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

  function step(now) {
    const t = Math.min((now - start) / DURATION, 1);
    const eased = ease(t);
    graph.forEachNode((id) => {
      const a = from.get(id);
      const b = positions[id];
      if (!a || !b) return;
      graph.setNodeAttribute(id, "x", a.x + (b.x - a.x) * eased);
      graph.setNodeAttribute(id, "y", a.y + (b.y - a.y) * eased);
    });
    if (t < 1) requestAnimationFrame(step);
    else if (done) done();
  }
  requestAnimationFrame(step);
}

// --- views --------------------------------------------------------------------
function buildViews(context) {
  const { graph, renderer, data, clusterPositions, clusterLabels } = context;
  const bar = el("view-switch");
  const note = el("view-note");
  const picker = el("focus-picker");
  if (!bar) return null;

  const seeds = data.nodes.filter((node) => node.is_seed);
  let focusId = (seeds[0] || data.nodes[0] || {}).id;
  let current = null;
  let moving = false;

  const VIEWS = {
    chain: {
      label: "Chain",
      note: "Left to right in the order the world happens: raw materials, the machines "
          + "that shape them, the chips, the boxes, and the companies that run them.",
      positions: () => chainLayout(graph, data),
    },
    clusters: {
      label: "Clusters",
      note: "Grouped by job, with companies pulled together by who they trade with. "
          + "Shows which jobs have many disclosed suppliers and which have almost none.",
      positions: () => clusterPositions,
    },
    focus: {
      label: "One buyer",
      note: "One company at the centre, everything else placed by how many steps away it "
          + "is. The outer ring is everything with no disclosed path to it at all.",
      positions: () => focusLayout(graph, focusId),
    },
  };

  const apply = (name) => {
    if (moving || !VIEWS[name] || name === current) return;
    current = name;
    moving = true;
    bar.querySelectorAll("button").forEach((button) => {
      const on = button.dataset.view === name;
      button.classList.toggle("active", on);
      button.setAttribute("aria-pressed", String(on));
    });
    if (note) note.textContent = VIEWS[name].note;
    clusterLabels.setVisible(name === "clusters");
    if (picker) picker.hidden = name !== "focus";
    glideTo(graph, VIEWS[name].positions(), () => { moving = false; });
    // Sigma normalises coordinates to the graph's bounding box on every frame,
    // so ratio 1 is "fitted" whatever the arrangement's own scale is. A layout
    // twice as wide as the last one would otherwise arrive off the edge of the
    // canvas, and a fixed ratio tuned for one view strands every other.
    renderer.getCamera().animate({ x: 0.5, y: 0.5, ratio: 1.3 }, { duration: 750 });
  };

  Object.entries(VIEWS).forEach(([name, view]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.view = name;
    button.textContent = view.label;
    button.addEventListener("click", () => apply(name));
    bar.appendChild(button);
  });

  if (picker && seeds.length) {
    seeds.forEach((seed) => {
      const option = document.createElement("option");
      option.value = seed.id;
      option.textContent = seed.canonical_name;
      picker.appendChild(option);
    });
    picker.value = focusId;
    picker.addEventListener("change", () => {
      focusId = picker.value;
      glideTo(graph, focusLayout(graph, focusId));
    });
  }

  apply("chain");
  return { apply, get current() { return current; } };
}

// --- search -------------------------------------------------------------------
function buildSearch(context) {
  const { graph, renderer, data, nodesById } = context;
  const input = el("search");
  const results = el("search-results");
  if (!input || !results) return;

  const nodes = data.nodes.slice()
    .sort((a, b) => a.canonical_name.localeCompare(b.canonical_name));
  const clear = () => { results.innerHTML = ""; results.hidden = true; };

  input.addEventListener("input", () => {
    const query = input.value.trim().toLowerCase();
    if (query.length < 2) return clear();
    const hits = nodes.filter((node) =>
      node.canonical_name.toLowerCase().includes(query)
      || (node.ticker || "").toLowerCase().includes(query)).slice(0, 8);
    results.innerHTML = hits.length
      ? hits.map((node) => `<button type="button" data-id="${escapeHtml(node.id)}">`
          + `${escapeHtml(node.canonical_name)}`
          + `<span class="tk">${escapeHtml(node.ticker || "")}</span></button>`).join("")
      : `<p class="hint">Nothing by that name. Every company here had to be named in a
           filing, so one you expect may simply never have been.</p>`;
    results.hidden = false;
  });

  results.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-id]");
    if (!button) return;
    // Camera coordinates are normalised rather than graph units, so ask the
    // renderer where the node is instead of doing the arithmetic here.
    const position = renderer.getNodeDisplayData(button.dataset.id);
    if (position) {
      renderer.getCamera().animate(
        { x: position.x, y: position.y, ratio: 0.45 }, { duration: 500 });
    }
    const node = nodesById.get(button.dataset.id);
    if (node) renderNode(node, data);
    input.value = "";
    clear();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { input.value = ""; clear(); input.blur(); }
  });
}

// --- hover --------------------------------------------------------------------
// Hovering a company lights the ones it trades with and dims the rest. On a
// graph this size that is the difference between a hairball and a readable
// answer to "who does this one actually deal with".
function buildHover(context) {
  const { graph, renderer } = context;
  let hovered = null;

  const paint = () => {
    graph.forEachNode((id, attributes) => {
      const near = hovered === null || id === hovered || graph.areNeighbors(id, hovered);
      graph.setNodeAttribute(id, "color", near ? attributes.hscmBaseColor : DIMMED);
    });
    graph.forEachEdge((edge, attributes, source, target) => {
      const near = hovered === null || source === hovered || target === hovered;
      graph.setEdgeAttribute(edge, "color", near ? attributes.hscmBaseColor : DIMMED);
    });
    renderer.refresh();
  };

  renderer.on("enterNode", ({ node }) => { hovered = node; paint(); });
  renderer.on("leaveNode", () => { hovered = null; paint(); });
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

  // Group companies by the job their own filings describe. Position carries
  // this: it has no discriminability ceiling the way colour does, and a
  // labelled group answers "what is this company for?" at a glance.
  const labels = data.meta?.function_labels || {};
  const present = Object.keys(labels).filter((key) =>
    data.nodes.some((node) => (node.function || "unstated") === key));
  const anchors = new Map(present.map((key, index) => {
    const angle = (2 * Math.PI * index) / present.length - Math.PI / 2;
    return [key, { x: Math.cos(angle) * 100, y: Math.sin(angle) * 100 }];
  }));
  const functionOf = (node) => (node.function || "unstated");

  // How much a company is talked about. Size carries it, because a reader
  // scanning the map should meet the heavily-disclosed companies first — and
  // because it is a fact about the evidence, not a claim about the company.
  const statements = new Map();
  data.edges.forEach((edge) => {
    const weight = edge.evidence?.length || 1;
    [edge.source, edge.target].forEach((id) =>
      statements.set(id, (statements.get(id) || 0) + weight));
  });

  data.nodes.forEach((node) => {
    // Colour answers one question: can this company's word be checked? A
    // company named in a filing but filing nothing itself is in the graph on
    // someone else's disclosure, and nothing it says can corroborate the edge.
    const color = node.is_seed
      ? SEED_COLOR
      : node.has_sec_filings === false
        ? NON_FILER_COLOR
        : SUPPLIER_COLOR;
    graph.addNode(node.id, {
      x: 0,
      y: 0,
      size: nodeSize(node, statements.get(node.id) || 0),
      label: node.canonical_name,
      color,
      hscmBaseColor: color,
      hscmFunction: functionOf(node),
      hscmSeed: Boolean(node.is_seed),
    });
  });

  data.edges.forEach((edge) => {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) return;
    const color = edge.quantified_pct == null ? EDGE_COLOR : EDGE_COLOR_QUANTIFIED;
    graph.addEdgeWithKey(edge.id, edge.source, edge.target, {
      size: edgeSize(edge.quantified_pct),
      color,
      hscmBaseColor: color,
      // No arrowhead when no filing stated which way anything flows. The stored
      // supplier/buyer order is a property of the record shape, not evidence.
      type: edge.direction_stated === false ? "line" : "arrow",
    });
  });

  const clusterPositions = computeClusterPositions(graph, anchors);

  const renderer = new Sigma(graph, el("graph"), {
    enableEdgeEvents: true, // sigma v3 does not emit edge clicks without this
    renderEdgeLabels: false,
    labelColor: { color: "#e6e8ec" },
    labelSize: 12,
    defaultEdgeType: "arrow",
    minCameraRatio: 0.08,
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

  const clusterLabels = buildClusterLabels(renderer, graph, present, labels, anchors);
  buildLegend(present, labels, graph, renderer, data.meta?.function_descriptions);

  const context = { graph, renderer, data, nodesById, clusterPositions, clusterLabels };
  const views = buildViews(context);
  buildSearch(context);
  buildHover(context);

  window.__graph = { graph, renderer, data, views }; // handle for tests and console
}

main();
