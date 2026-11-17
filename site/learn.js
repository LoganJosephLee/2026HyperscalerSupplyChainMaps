/* Learning map. Reads window.MAP_DATA (data/map.js, written by `hscm learn`). */
(function () {
  "use strict";

  const data = window.MAP_DATA;
  if (!data) {
    document.getElementById("layers").innerHTML =
      '<p class="empty">The map data is missing. Run <code>hscm learn</code> to build <code>site/data/map.js</code>.</p>';
    return;
  }

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fmtDate = (iso) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : "";
  };

  const nodes = new Map(data.nodes.map((n) => [n.id, n]));
  // Plain #names only: some hosts pass nothing else through the URL.
  const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const bySlug = new Map(data.nodes.map((n) => [slug(n.ref), n]));
  const layers = new Map(data.layers.map((l) => [l.key, l]));
  const edgesOf = new Map(data.nodes.map((n) => [n.id, []]));
  for (const e of data.edges) {
    edgesOf.get(e.source)?.push(e);
    edgesOf.get(e.target)?.push(e);
  }
  const relLabel = (r) => data.meta.relationship_labels[r] || r;
  const typeLabel = (t) => data.meta.source_types[t] || t;

  // --- header, method, glossary -------------------------------------------
  const m = data.meta;
  $("counts").textContent =
    `${m.company_count} companies, ${m.relationship_count} connections between them, and ${m.citation_count} quoted sources ` +
    `(${m.citations_by_type.sec_filing} from SEC filings, ${m.citations_by_type.company} from company announcements, ` +
    `${m.citations_by_type.press} from news reports).`;
  $("stamp").textContent =
    `Newest source: ${fmtDate(m.newest_source_date)}. SEC filings read up to ${fmtDate(m.sec_data_as_of)}. Map rebuilt ${fmtDate(m.built)}.`;

  const KIND_TEXT = {
    sec_filing: "A report the company filed with the US Securities and Exchange Commission. Companies can be penalised for misstatements in these, which makes them the strongest source here.",
    company: "A press release or official blog post from one of the companies in the deal.",
    press: "Reporting by a major news organisation. Where the companies have not confirmed a deal themselves, it is marked as reported, not confirmed.",
  };
  $("source-kinds").innerHTML = Object.keys(m.source_types)
    .map((t) => `<dt><span class="badge ${t}">${esc(typeLabel(t))}</span></dt><dd>${esc(KIND_TEXT[t])}</dd>`)
    .join("");
  $("glossary").innerHTML = data.glossary
    .map((g) => `<div><dt>${esc(g.term)}</dt><dd>${esc(g.definition)}</dd></div>`)
    .join("");

  // --- the stack -------------------------------------------------------------
  const chipHtml = (n) => {
    const cls = ["chip"];
    if (n.is_seed) cls.push("seed");
    if (!n.files_with_sec) cls.push("nonfiler");
    const filer = n.files_with_sec ? "files with the SEC" : "files nothing with the SEC";
    return `<button type="button" class="${cls.join(" ")}" data-id="${esc(n.id)}"
      aria-label="${esc(n.name)}, ${n.degree} cited connections, ${filer}">${esc(n.name)}<span class="deg" aria-hidden="true">${n.degree}</span></button>`;
  };
  const membersOf = (key) =>
    data.nodes.filter((n) => n.layer === key).sort((a, b) => b.degree - a.degree || a.name.localeCompare(b.name));

  $("layers").innerHTML = data.layers
    .filter((l) => !l.side)
    .map((l) => `
      <section class="stratum" data-layer="${esc(l.key)}" aria-labelledby="layer-${esc(l.key)}">
        <div><h3 id="layer-${esc(l.key)}">${esc(l.label)}</h3><p>${esc(l.blurb)}</p></div>
        <div class="chips">${membersOf(l.key).map(chipHtml).join("")}</div>
      </section>`)
    .join("");
  $("side-layers").innerHTML =
    `<h3 class="side-heading">Also on the map</h3>` +
    data.layers
      .filter((l) => l.side)
      .map((l) => `
        <section class="side-layer" data-layer="${esc(l.key)}">
          <h3>${esc(l.label)}</h3><p>${esc(l.blurb)}</p>
          <div class="chips">${membersOf(l.key).map(chipHtml).join("")}</div>
        </section>`)
      .join("");

  const chipEl = (id) => document.querySelector(`.chip[data-id="${CSS.escape(id)}"]`);

  // --- connection lines --------------------------------------------------------
  const svg = $("links");
  function drawLinks(pairs) {
    const box = $("stack").getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
    svg.innerHTML =
      `<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="#2443c7"/></marker></defs>` +
      pairs
        .map(({ edge, tour }) => {
          const a = chipEl(edge.source), b = chipEl(edge.target);
          if (!a || !b || !a.offsetParent || !b.offsetParent) return "";
          const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
          // Lines only connect chips inside the stack; side layers sit outside its box.
          if (!$("stack").contains(a) || !$("stack").contains(b)) return "";
          const x1 = ra.left + ra.width / 2 - box.left, y1 = ra.top + ra.height / 2 - box.top;
          const x2 = rb.left + rb.width / 2 - box.left, y2 = rb.top + rb.height / 2 - box.top;
          const dy = Math.abs(y2 - y1), bend = Math.max(40, dy * 0.45);
          const sameRow = dy < 8;
          const d = sameRow
            ? `M${x1},${y1 - 12} C${x1},${y1 - 60} ${x2},${y2 - 60} ${x2},${y2 - 12}`
            : `M${x1},${y1} C${x1 + (x2 > x1 ? bend : -bend) * 0.3},${y1 + (y2 > y1 ? bend : -bend)} ${x2},${y2 - (y2 > y1 ? bend : -bend)} ${x2},${y2}`;
          const cls = [edge.kind === "supply" ? "" : edge.kind, tour ? "tour" : ""].join(" ").trim();
          const marker = edge.kind === "undirected" ? "" : ' marker-end="url(#arrow)"';
          return `<path d="${d}" class="${cls}"${marker}/>`;
        })
        .join("");
  }
  const clearLinks = () => (svg.innerHTML = "");

  // --- focus a company -------------------------------------------------------------
  let focusId = null;
  function setFocus(id, linkedIds) {
    document.querySelectorAll(".chip.is-focus, .chip.is-linked").forEach((c) => c.classList.remove("is-focus", "is-linked"));
    const root = document.body;
    if (!id) {
      root.classList.remove("has-focus");
      clearLinks();
      return;
    }
    root.classList.add("has-focus");
    chipEl(id)?.classList.add("is-focus");
    for (const other of linkedIds) chipEl(other)?.classList.add("is-linked");
  }

  function citeHtml(c) {
    const v = c.verified || {};
    const checked =
      v.status === "found"
        ? `Checked: ${esc(v.method)}${v.checked ? ", " + fmtDate(v.checked) : ""}.`
        : v.status === "unchecked"
        ? "Not yet checked against the live page."
        : `Last check: ${esc(v.status.replace("_", " "))}${v.checked ? ", " + fmtDate(v.checked) : ""}.`;
    return `<div class="cite">
      ${c.context ? `<p class="context">${esc(c.context)}</p>` : ""}
      <blockquote>${esc(c.quote)}</blockquote>
      <p class="src"><span class="badge ${esc(c.type)}">${esc(typeLabel(c.type))}</span>
        ${esc(c.publisher)}${c.date ? ", " + fmtDate(c.date) : ""}.
        <a href="${esc(c.url)}" target="_blank" rel="noopener">Open the source</a></p>
      ${c.note ? `<p class="checked">Correction: ${esc(c.note)}</p>` : ""}
      <p class="checked${v.status === "found" ? "" : " warn"}">${checked}</p>
    </div>`;
  }

  function dealHtml(edge, deal, selfId) {
    const s = nodes.get(edge.source), t = nodes.get(edge.target);
    const who = (n) =>
      n.id === selfId ? `<strong>${esc(n.name)}</strong>` : `<button type="button" class="who" data-id="${esc(n.id)}">${esc(n.name)}</button>`;
    const types = [...new Set(deal.evidence.map((c) => c.type))];
    const badges =
      types.map((tp) => `<span class="badge ${tp}">${esc(typeLabel(tp))}</span>`).join("") +
      (deal.confirmed ? "" : `<span class="badge unconfirmed">Reported, not confirmed by the companies</span>`);
    const n = deal.evidence.length;
    return `<div class="deal">
      <p class="deal-line">${who(s)} ${esc(relLabel(deal.relationship))} ${who(t)}</p>
      ${deal.what || deal.amount ? `<p class="deal-what">${esc(deal.what || "")}${deal.amount && !(deal.what || "").includes(deal.amount) ? ` <span class="amount">(${esc(deal.amount)})</span>` : ""}</p>` : ""}
      <div class="badges">${badges}</div>
      <details class="quotes"><summary>Show the ${n === 1 ? "quote" : n + " quotes"}</summary>${deal.evidence.map(citeHtml).join("")}</details>
    </div>`;
  }

  function openCompany(id, { pushHash = true } = {}) {
    const n = nodes.get(id);
    if (!n) return;
    endTour({ keepFocus: true });
    focusId = id;
    const mine = edgesOf.get(id) || [];
    const linked = mine.map((e) => (e.source === id ? e.target : e.source));
    setFocus(id, linked);
    requestAnimationFrame(() => drawLinks(mine.map((edge) => ({ edge }))));

    const gives = mine.filter((e) => e.source === id);
    const gets = mine.filter((e) => e.target === id);
    const section = (title, list, empty) =>
      `<h3>${title}</h3>` +
      (list.length ? list.flatMap((e) => e.deals.map((d) => dealHtml(e, d, id))).join("") : `<p class="none">${empty}</p>`);
    const layer = layers.get(n.layer);
    const anySecLabel = mine.some((e) => e.deals.some((d) => d.what_is_label));

    $("drawer-body").innerHTML = `
      <span class="layer-tag">${esc(layer ? layer.label : n.layer)}</span>
      <h2 id="drawer-title">${esc(n.name)}</h2>
      <p class="legal">${n.legal_name && n.legal_name.toLowerCase() !== n.name.toLowerCase() ? esc(n.legal_name) + ". " : ""}${
        n.ticker ? "Ticker " + esc(n.ticker) + ". " : ""}${n.files_with_sec ? "Files reports with the SEC." : "Files nothing with the SEC; it appears here because other companies named it."}</p>
      <p class="summary">${esc(n.summary)}</p>
      ${n.about ? `<details class="quotes"><summary>Where this description comes from</summary>${citeHtml(n.about)}</details>` : ""}
      ${section(`What ${esc(n.name)} provides`, gives, "No cited source names a company it supplies.")}
      ${section(`What ${esc(n.name)} gets from others`, gets, "No cited source names a company that supplies it.")}
      ${anySecLabel ? `<p class="label-note">For relationships found in SEC filings, the short description is a summary written by the extraction model. The quote underneath is the evidence.</p>` : ""}`;
    const drawer = $("drawer");
    drawer.hidden = false;
    document.body.classList.add("drawer-open");
    requestAnimationFrame(() => drawLinks(mine.map((edge) => ({ edge }))));
    drawer.scrollTop = 0;
    $("drawer-close").focus({ preventScroll: true });
    if (pushHash) history.replaceState(null, "", `#c-${slug(n.ref)}`);
  }

  function closeCompany() {
    $("drawer").hidden = true;
    document.body.classList.remove("drawer-open");
    focusId = null;
    setFocus(null);
    history.replaceState(null, "", location.pathname + location.search);
  }

  document.addEventListener("click", (ev) => {
    const target = ev.target.closest("[data-id]");
    if (target) {
      ev.preventDefault();
      openCompany(target.dataset.id);
      if (target.classList.contains("who")) chipEl(target.dataset.id)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  });
  $("drawer-close").addEventListener("click", closeCompany);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      if (!$("tourbar").hidden) endTour();
      else if (!$("drawer").hidden) closeCompany();
    }
  });
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (tour) showStep();
      else if (focusId) drawLinks((edgesOf.get(focusId) || []).map((edge) => ({ edge })));
    }, 120);
  });

  // --- search ------------------------------------------------------------------
  const norm = (s) => s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, " ").trim();
  function matches(q) {
    const k = norm(q);
    if (!k) return [];
    return data.nodes.filter((n) => [n.name, n.legal_name, n.ticker, n.ref].some((v) => v && norm(v).includes(k)));
  }
  $("search").addEventListener("input", (ev) => {
    const hits = new Set(matches(ev.target.value).map((n) => n.id));
    document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("is-match", hits.has(c.dataset.id)));
  });
  $("search-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const hit = matches($("search").value)[0];
    if (hit) {
      chipEl(hit.id)?.scrollIntoView({ block: "center", behavior: "smooth" });
      openCompany(hit.id);
    }
  });

  // --- tours ---------------------------------------------------------------------
  $("tour-list").innerHTML = data.tours
    .map((t) => `<button type="button" class="tour-card" data-tour="${esc(t.id)}">
      <strong>${esc(t.title)}</strong><span>${esc(t.intro)}</span><em>${t.steps.length} stops</em></button>`)
    .join("");
  document.querySelectorAll("[data-tour]").forEach((b) => b.addEventListener("click", () => startTour(b.dataset.tour)));

  let tour = null, step = 0;
  const edgeById = new Map(data.edges.map((e) => [e.id, e]));
  function startTour(id) {
    tour = data.tours.find((t) => t.id === id);
    if (!tour) return;
    $("drawer").hidden = true;
    document.body.classList.remove("drawer-open");
    step = 0;
    $("tourbar").hidden = false;
    history.replaceState(null, "", `#tour-${id}`);
    showStep();
  }
  function showStep() {
    const s = tour.steps[step];
    const edge = edgeById.get(s.edge);
    $("tour-progress").textContent = `${tour.title}, stop ${step + 1} of ${tour.steps.length}`;
    $("tour-title").textContent = `${nodes.get(s.from_id).name} and ${nodes.get(s.to_id).name}`;
    $("tour-text").textContent = s.text;
    $("tour-cites").innerHTML = edge.deals.flatMap((d) => d.evidence).map(citeHtml).join("");
    $("tour-source").open = false;
    $("tour-prev").disabled = step === 0;
    $("tour-next").textContent = step === tour.steps.length - 1 ? "Finish" : "Next";
    setFocus(s.from_id, [s.to_id]);
    chipEl(s.to_id)?.classList.add("is-focus");
    const a = chipEl(s.from_id);
    a?.scrollIntoView({ block: "center", behavior: "smooth" });
    setTimeout(() => drawLinks([{ edge, tour: true }]), 350);
  }
  function endTour({ keepFocus = false } = {}) {
    if (!tour) return;
    tour = null;
    $("tourbar").hidden = true;
    if (!keepFocus) {
      setFocus(null);
      history.replaceState(null, "", location.pathname + location.search);
    }
  }
  $("tour-prev").addEventListener("click", () => { if (step > 0) { step--; showStep(); } });
  $("tour-next").addEventListener("click", () => {
    if (step < tour.steps.length - 1) { step++; showStep(); } else endTour();
  });
  $("tour-exit").addEventListener("click", () => endTour());

  // --- deep links -----------------------------------------------------------------
  function route() {
    const h = decodeURIComponent(location.hash.slice(1));
    if (h.startsWith("c-")) {
      const n = bySlug.get(h.slice(2));
      if (n && n.id !== focusId) { chipEl(n.id)?.scrollIntoView({ block: "center" }); openCompany(n.id, { pushHash: false }); }
    } else if (h.startsWith("tour-") && (!tour || tour.id !== h.slice(5))) {
      startTour(h.slice(5));
    }
  }
  window.addEventListener("hashchange", route);
  setTimeout(route, 50);
})();
