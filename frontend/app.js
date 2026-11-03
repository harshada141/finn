/* FINN — mobile-first triaged investor inbox. No build step: plain JS + SVG. */
(() => {
  "use strict";

  // ------------------------------------------------------------ state & storage
  const store = {
    get(k, d) { try { const v = localStorage.getItem("finn." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("finn." + k, JSON.stringify(v)); } catch { /* private mode */ } },
  };
  const S = {
    config: null,
    hours: store.get("hours", 24),
    portfolio: store.get("portfolio", null),
    price: store.get("price", 5),
    volume: store.get("volume", 2),
    cats: store.get("cats", null),
    cleared: store.get("cleared", {}),
    showCleared: false,
    digest: null,
    alerts: null,
    research: {},
  };
  const $view = document.getElementById("view");
  const symbolsParam = () => (S.portfolio || S.config.portfolio).join(",");

  // ------------------------------------------------------------ helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const inr = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
  const sign = (v) => v == null ? "–" : (v > 0 ? "+" : "") + v.toFixed(1) + "%";
  const dir = (v) => v == null ? "flat" : v > 0.05 ? "up" : v < -0.05 ? "down" : "flat";
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch { } throw new Error(m); }
    return r.json();
  }
  function ago(s) {
    const t = new Date(s.replace(" ", "T") + "+05:30");
    const m = Math.round((Date.now() - t) / 60000);
    if (m < 60) return `${Math.max(m, 1)}m ago`;
    if (m < 1440) return `${Math.round(m / 60)}h ago`;
    return `${Math.round(m / 1440)}d ago`;
  }
  // Exchange subjects are wordy: "X Limited has informed the Exchange about ..."
  function cleanSubject(s) {
    let t = (s || "").replace(/^.*?has (?:informed|submitted to) the Exchange(?: about| regarding| that)?\s*/i, "")
      .replace(/^[\s,.;:-]*(?:the\s)?/i, "").replace(/^./, (c) => c.toUpperCase());
    if (t.length > 170) t = t.slice(0, 165).replace(/\s+\S*$/, "") + "…";
    return t;
  }
  const CAT_LABEL = { "M&A": "M&A", fund_raise: "Fund raise", management_change: "Management", earnings_call: "Concall",
    investor_meetings: "Investor meet", credit_rating: "Rating", bonus_split: "Bonus/split", press_release: "Business update",
    postal_ballot: "Postal ballot", agm_egm: "AGM/EGM", insider_trading: "Insider/SAST", unclassified: "Routine" };
  const catLabel = (c) => CAT_LABEL[c] || c.replace(/_/g, " ");

  function spark(vals, w = 120, h = 30, color = "var(--brand)") {
    const v = (vals || []).filter((x) => x != null);
    if (v.length < 2) return "";
    const mn = Math.min(...v), mx = Math.max(...v), r = mx - mn || 1;
    const pts = v.map((x, i) => `${(i / (v.length - 1) * (w - 4) + 2).toFixed(1)},${(h - 3 - (x - mn) / r * (h - 6)).toFixed(1)}`);
    const last = pts[pts.length - 1].split(",");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" points="${pts.join(" ")}"/><circle cx="${last[0]}" cy="${last[1]}" r="2.6" fill="${color}"/></svg>`;
  }

  // grouped bars (a, b) per label + optional line (pct axis on right)
  function barChart({ labels, a, b, line, aName, bName, lineName, aColor = "var(--brand)", bColor = "var(--ink-3)" }) {
    const W = 340, H = 150, P = { l: 6, r: line ? 30 : 6, t: 10, b: 22 };
    const n = labels.length; if (!n) return "";
    const all = [...(a || []), ...(b || [])].filter((x) => x != null);
    const mx = Math.max(...all, 0), mn = Math.min(...all, 0), rng = (mx - mn) || 1;
    const cw = (W - P.l - P.r) / n, bw = b ? cw * 0.34 : cw * 0.56;
    const y = (v) => P.t + (mx - v) / rng * (H - P.t - P.b);
    let s = `<line x1="${P.l}" x2="${W - P.r}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line)"/>`;
    labels.forEach((lab, i) => {
      const x0 = P.l + i * cw + (cw - (b ? bw * 2 + 2 : bw)) / 2;
      const bar = (v, x, c) => v == null ? "" : `<rect x="${x.toFixed(1)}" y="${Math.min(y(v), y(0)).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(Math.abs(y(v) - y(0)), 1).toFixed(1)}" rx="2" fill="${c}"><title>${esc(lab)}: ${inr(v)}</title></rect>`;
      s += bar(a?.[i], x0, aColor);
      if (b) s += bar(b[i], x0 + bw + 2, bColor);
      if (n <= 6 || i % 2 === (n - 1) % 2) s += `<text x="${(P.l + i * cw + cw / 2).toFixed(1)}" y="${H - 6}" font-size="9" text-anchor="middle" fill="var(--ink-3)">${esc(lab.replace(/^Mar \d{2}(\d{2})$/, "'$1").replace(/^([A-Za-z]{3}) \d{2}(\d{2})$/, "$1 '$2"))}</text>`;
    });
    if (line && line.some((x) => x != null)) {
      const lv = line.filter((x) => x != null), lmx = Math.max(...lv), lmn = Math.min(...lv), lr = (lmx - lmn) || 1;
      const ly = (v) => P.t + 6 + (lmx - v) / lr * (H - P.t - P.b - 12);
      const pts = line.map((v, i) => v == null ? null : `${(P.l + i * cw + cw / 2).toFixed(1)},${ly(v).toFixed(1)}`).filter(Boolean);
      s += `<polyline fill="none" stroke="var(--know)" stroke-width="2" points="${pts.join(" ")}"/>`;
      line.forEach((v, i) => { if (v != null) s += `<circle cx="${(P.l + i * cw + cw / 2).toFixed(1)}" cy="${ly(v).toFixed(1)}" r="2.4" fill="var(--know)"><title>${esc(lineName)} ${v}%</title></circle>`; });
      s += `<text x="${W - 2}" y="${ly(lmx) + 3}" font-size="9" text-anchor="end" fill="var(--know)">${lmx}%</text><text x="${W - 2}" y="${ly(lmn) + 3}" font-size="9" text-anchor="end" fill="var(--know)">${lmn}%</text>`;
    }
    const legend = `<div class="legend"><span><i style="background:${aColor}"></i>${esc(aName)}</span>${b ? `<span><i style="background:${bColor}"></i>${esc(bName)}</span>` : ""}${line ? `<span><i style="background:var(--know)"></i>${esc(lineName)}</span>` : ""}</div>`;
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(aName)} chart">${s}</svg>${legend}`;
  }

  // ------------------------------------------------------------ shared: results grid
  function resultsGrid(rc) {
    if (!rc) return "";
    const m = (lab, k) => {
      const d = rc[k]; if (!d) return "";
      return `<div class="metric"><div class="lab">${lab}${spark(d.spark, 46, 16, "var(--ink-3)")}</div>
        <div class="val">₹${inr(d.value)}<small class="muted" style="font:400 11px Inter"> Cr</small></div>
        <div class="deltas"><span class="delta ${dir(d.qoq)}">QoQ ${sign(d.qoq)}</span><span class="delta ${dir(d.yoy)}">YoY ${sign(d.yoy)}</span></div></div>`;
    };
    const ebitdaLab = rc.ebitda ? "EBITDA" : "";
    return `<div class="metrics">${m("Revenue", "revenue")}${m(ebitdaLab, "ebitda")}${m("PBT", "pbt")}${m("PAT", "pat")}</div>
      <div class="qlabel">${esc(rc.quarter_fy || "")} (${esc(rc.quarter)}) vs ${esc(rc.prev_quarter)} and ${esc(rc.year_ago_quarter || "–")}${rc.margin != null ? ` · Op. margin <b class="num">${rc.margin}%</b>${rc.margin_prev_year != null ? ` vs ${rc.margin_prev_year}% a year ago` : ""}` : ""} · <a href="${esc(rc.source)}" target="_blank" rel="noopener">source</a></div>`;
  }

  function peopleBlock(mg) {
    if (!mg || !(mg.people || []).length) return "";
    return mg.people.slice(0, 3).map((p) => `<div class="person">
      <div class="who"><b>${esc(p.name)}</b><span class="pill ${esc((p.change || "").toLowerCase())}">${esc(p.change || "")}</span></div>
      <div class="card-sub">${esc(p.role || "")}${p.effective ? ` · effective ${esc(p.effective)}` : ""}${p.reason ? ` · ${esc(p.reason)}` : ""}</div>
      ${(p.bio || []).length ? `<ul>${p.bio.slice(0, 4).map((b) => `<li>${esc(b)}</li>`).join("")}</ul>` : `<div class="card-sub muted">No profile in the filing.</div>`}
    </div>`).join("");
  }

  // ------------------------------------------------------------ BRIEF
  async function renderBrief() {
    const chips = [24, 72, 168, 720].map((h) => `<button class="chip ${S.hours === h ? "on" : ""}" data-hours="${h}">${h < 168 ? h + "h" : h === 168 ? "7 days" : "30 days"}</button>`).join("");
    const hr = new Date().getHours();
    $view.innerHTML = `<h1>${hr < 12 ? "Good morning" : hr < 17 ? "Good afternoon" : "Good evening"}</h1>
      <p class="sub">Everything your ${(S.portfolio || S.config.portfolio).length} holdings filed, triaged so you read only what matters.</p>
      <div class="chips" role="tablist">${chips}</div><div id="brief-body">${skeleton(5)}</div>`;
    $view.querySelectorAll("[data-hours]").forEach((b) => b.onclick = () => { S.hours = +b.dataset.hours; store.set("hours", S.hours); S.digest = null; renderBrief(); });
    try {
      if (!S.digest || S.digest.hours !== S.hours) S.digest = await api(`/api/digest?symbols=${symbolsParam()}&hours=${S.hours}`);
      drawBrief();
      if (S.digest.pending.length) enrichBrief();
    } catch (e) { document.getElementById("brief-body").innerHTML = `<div class="err">Couldn't load filings: ${esc(e.message)}</div>`; }
  }

  async function enrichBrief() {
    const el = document.getElementById("reading");
    if (el) el.hidden = false;
    try {
      await api(`/api/digest/enrich?symbols=${symbolsParam()}&hours=${S.hours}`, { method: "POST" });
      S.digest = await api(`/api/digest?symbols=${symbolsParam()}&hours=${S.hours}`);
      if (location.hash.startsWith("#brief") || !location.hash) drawBrief();
    } catch (e) { if (el) el.innerHTML = `<span class="muted">Summaries unavailable right now — showing raw filings.</span>`; }
  }

  function drawBrief() {
    const d = S.digest, body = document.getElementById("brief-body");
    if (!body) return;
    const items = d.items.map((it) => {
      const e = it.enrichment || {};
      return { ...it, tier: e.tier || it.tier, category: e.category || it.category };
    });
    const live = items.filter((i) => i.tier !== "routine");
    const open = live.filter((i) => !S.cleared[i.id]);
    const done = live.length - open.length;
    const act = open.filter((i) => i.tier === "act"), know = open.filter((i) => i.tier === "know");
    const routine = items.filter((i) => i.tier === "routine");
    setBadge("brief", act.length);

    const pct = live.length ? done / live.length : 1;
    const ring = `<svg width="44" height="44" viewBox="0 0 44 44"><circle cx="22" cy="22" r="18" fill="none" stroke="var(--line)" stroke-width="5"/><circle cx="22" cy="22" r="18" fill="none" stroke="var(--brand)" stroke-width="5" stroke-linecap="round" stroke-dasharray="${(pct * 113.1).toFixed(1)} 113.1" transform="rotate(-90 22 22)"/></svg>`;
    const head = `<div class="progress">${ring}<div><b>${open.length ? `${open.length} to read` : "You're done ✓"}</b>
      <span>${d.items.length} filings → ${act.length} act now · ${know.length} worth knowing · ${routine.length} routine hidden</span></div></div>
      <div class="reading" id="reading" ${d.pending.length ? "" : "hidden"}><span class="spinner"></span>FINN is reading ${d.pending.length} filings…</div>`;

    let html = head;
    if (!live.length && !routine.length) {
      html += `<div class="empty" style="margin-top:14px"><b>Quiet window</b>No filings from your portfolio in this period. Try 7 days.</div>`;
    }
    if (act.length) html += `<h2><span class="dot" style="background:var(--act)"></span>Act now</h2>` + act.map((i) => card(i)).join("");
    if (know.length) html += `<h2><span class="dot" style="background:var(--know)"></span>Worth knowing</h2>` + know.map((i) => card(i)).join("");
    if (!open.length && live.length) html += `<div class="empty" style="margin-top:14px"><b>Inbox zero</b>Everything material is read. Routine paperwork is below if you want it.</div>`;
    if (routine.length) {
      html += `<h2><span class="dot" style="background:var(--routine)"></span>Routine</h2>
        <details class="routine"><summary>${routine.length} compliance filings, collapsed</summary>
        ${routine.map((i) => `<a class="row" href="${esc(i.pdf)}" target="_blank" rel="noopener"><span class="tkr">${esc(i.symbol)}</span><span class="txt">${esc(i.enrichment?.one_liner || cleanSubject(i.subject) || i.nse_category)}</span><span class="cat">${esc(catLabel(i.category))}</span></a>`).join("")}
        </details>`;
    }
    if (done) {
      html += `<p style="text-align:center;margin-top:16px"><button class="btn" id="toggle-cleared">${S.showCleared ? "Hide" : "Show"} ${done} cleared</button></p>`;
      if (S.showCleared) html += live.filter((i) => S.cleared[i.id]).map((i) => card(i, true)).join("");
    }
    body.innerHTML = html;
    body.querySelectorAll("[data-clear]").forEach((b) => b.onclick = () => {
      const id = b.dataset.clear, c = b.closest(".card");
      if (S.cleared[id]) delete S.cleared[id]; else S.cleared[id] = Date.now();
      store.set("cleared", S.cleared);
      c.classList.add("leaving"); setTimeout(drawBrief, 220);
    });
    const t = document.getElementById("toggle-cleared");
    if (t) t.onclick = () => { S.showCleared = !S.showCleared; drawBrief(); };
  }

  function card(it, cleared = false) {
    const e = it.enrichment || {};
    const title = e.management?.headline || e.one_liner || cleanSubject(it.subject) || it.nse_category;
    const showSub = e.one_liner && e.management?.headline && e.one_liner !== e.management.headline;
    const related = (it.related || []).length ? `<div class="card-sub muted">+${it.related.length} related filing${it.related.length > 1 ? "s" : ""} folded in (${[...new Set(it.related.map((r) => r.nse_category))].map(esc).join(", ")})</div>` : "";
    return `<article class="card tier-${it.tier}">
      <div class="card-head"><span class="tkr">${esc(it.symbol)}</span><span class="cat">${esc(catLabel(it.category))}</span><span class="time">${ago(it.time)}</span></div>
      <p class="card-title">${esc(title)}</p>
      ${showSub ? `<p class="card-sub">${esc(e.one_liner)}</p>` : ""}
      ${!e.one_liner && !e.management ? `<p class="card-sub muted">${esc(it.nse_category)}</p>` : ""}
      ${it.results_card ? resultsGrid(it.results_card) : ""}
      ${peopleBlock(e.management)}
      ${related}
      <div class="card-actions">
        ${it.pdf && it.pdf.startsWith("http") ? `<a class="btn" href="${esc(it.pdf)}" target="_blank" rel="noopener">Filing ↗</a>` : ""}
        <a class="btn" href="#research/${encodeURIComponent(it.symbol)}">Ask FINN →</a>
        <button class="btn done ${cleared ? "" : "primary"}" data-clear="${esc(it.id)}">${cleared ? "Undo" : "✓ Done"}</button>
      </div></article>`;
  }

  // ------------------------------------------------------------ ALERTS
  async function renderAlerts() {
    const cats = S.cats || S.config.default_alert_categories;
    $view.innerHTML = `<h1>Alerts</h1><p class="sub">End-of-day moves and material filings that crossed <em>your</em> lines.</p>
      <div class="thresholds">Watching <b>±${S.price}%</b> price · <b>${S.volume}×</b> volume · <b>${cats.length}</b> filing types
        <button class="btn" style="margin-left:auto" id="edit-th">Edit</button></div>
      <div id="alerts-body">${skeleton(4)}</div>`;
    document.getElementById("edit-th").onclick = openSettings;
    try {
      S.alerts = await api(`/api/alerts?symbols=${symbolsParam()}&price=${S.price}&volume=${S.volume}&cats=${encodeURIComponent(cats.join(","))}`);
      drawAlerts();
    } catch (e) { document.getElementById("alerts-body").innerHTML = `<div class="err">Couldn't load alerts: ${esc(e.message)}</div>`; }
  }

  let pxSort = "move";
  function drawAlerts() {
    const a = S.alerts, el = document.getElementById("alerts-body");
    setBadge("alerts", a.alerts.filter((x) => x.severity === "act").length);
    const ICON = { price: "₹", volume: "▮▮", filing: "§" };
    let html = `<h2>Triggered${a.alerts.length ? ` · ${a.alerts.length}` : ""}</h2>`;
    html += a.alerts.length ? a.alerts.map((x) => `<article class="card alert ${x.severity}">
        <div class="ico">${ICON[x.type]}</div>
        <div class="body"><p class="card-title">${esc(x.headline)}</p><p class="card-sub">${esc(x.detail)}</p>
          <div class="card-actions" style="margin-top:8px">${x.pdf ? `<a class="btn" href="${esc(x.pdf)}" target="_blank" rel="noopener">Filing ↗</a>` : ""}<a class="btn" href="#research/${encodeURIComponent(x.symbol)}">Ask FINN →</a></div></div>
        ${x.spark ? `<div>${spark(x.spark, 64, 30, x.pct < 0 ? "var(--down)" : "var(--up)")}</div>` : ""}
      </article>`).join("")
      : `<div class="empty"><b>Nothing crossed your thresholds</b>FINN checks end-of-day prices and the exchange feed. Loosen the thresholds to see more.</div>`;

    const rows = Object.entries(a.prices).map(([s, d]) => ({ s, ...d }));
    rows.sort((x, y) => pxSort === "vol" ? (y.vol_ratio || 0) - (x.vol_ratio || 0) : pxSort === "sym" ? x.s.localeCompare(y.s) : Math.abs(y.pct) - Math.abs(x.pct));
    const asOf = rows[0]?.date ? ` · as of ${rows[0].date}` : "";
    html += `<h2>Portfolio, end of day${asOf}</h2><table class="px"><thead><tr><th data-sort="sym">Stock</th><th>Close</th><th data-sort="move">Move ↓</th><th data-sort="vol">Vol vs 20d</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td><a href="#research/${encodeURIComponent(r.s)}" class="tkr" style="text-decoration:none">${esc(r.s)}</a></td><td class="num">${inr(r.close, 1)}</td>
        <td class="num ${Math.abs(r.pct) >= S.price ? "hot" : ""}" style="color:var(--${dir(r.pct)})">${sign(r.pct)}</td>
        <td class="num ${r.vol_ratio >= S.volume ? "hot" : ""}">${r.vol_ratio ?? "–"}×</td></tr>`).join("")}</tbody></table>`;
    el.innerHTML = html;
    el.querySelectorAll("[data-sort]").forEach((th) => th.onclick = () => { pxSort = th.dataset.sort; drawAlerts(); });
  }

  // ------------------------------------------------------------ RESEARCH
  async function renderResearch(sym) {
    const port = S.portfolio || S.config.portfolio;
    $view.innerHTML = `<h1>Research</h1><p class="sub">Any NSE ticker. Built only from filed financials and concall transcripts — every claim cites its source.</p>
      <form class="search" id="rs"><input id="rq" placeholder="e.g. PERSISTENT" value="${esc(sym || "")}" autocomplete="off" autocapitalize="characters" spellcheck="false" aria-label="NSE ticker"><button class="btn primary">Go</button></form>
      <div class="chips">${S.config.research_demo.map((t) => `<a class="chip ${t === sym ? "on" : ""}" href="#research/${t}">${t}</a>`).join("")}<span class="chip" style="border:0;background:none;padding-left:2px;padding-right:2px">·</span>${port.map((t) => `<a class="chip ${t === sym ? "on" : ""}" href="#research/${t}">${t}</a>`).join("")}</div>
      <div id="rbody">${sym ? skeleton(6) : `<div class="empty" style="margin-top:14px"><b>Pick a company</b>Try one outside your portfolio — the engine isn't hard-wired to the demo set.</div>`}</div>`;
    document.getElementById("rs").onsubmit = (ev) => { ev.preventDefault(); const v = document.getElementById("rq").value.trim().toUpperCase(); if (v) location.hash = "#research/" + encodeURIComponent(v); };
    if (!sym) return;
    try {
      const n = S.research[sym]?.numbers || await api(`/api/research/${encodeURIComponent(sym)}`);
      S.research[sym] = { ...(S.research[sym] || {}), numbers: n };
      if (currentSym() !== sym) return;
      drawNumbers(n);
      loadNarrative(sym);
    } catch (e) { document.getElementById("rbody").innerHTML = `<div class="err">${esc(e.message)}. Check the ticker is an NSE symbol (e.g. HDFCBANK, not HDFC Bank).</div>`; }
  }
  const currentSym = () => decodeURIComponent((location.hash.split("/")[1] || "")).toUpperCase();

  function drawNumbers(n) {
    const r = n.ratios || {};
    const pick = (k) => r[k] ? esc(r[k].replace("₹", "").replace(" Cr.", "").trim()) : "–";
    const t = n.trajectory, cq = n.cash_quality, bs = n.balance_sheet;
    let html = `<div class="co-head"><div class="row1"><div><h1 style="font-size:21px">${esc(n.name)}</h1><span class="tkr">${esc(n.symbol)}</span></div><div class="px">₹${pick("Current Price")}</div></div></div>
      <div class="ratios">
        <div class="ratio"><small>Mkt cap ₹Cr</small><b>${pick("Market Cap")}</b></div>
        <div class="ratio"><small>P/E</small><b>${pick("Stock P/E")}</b></div>
        <div class="ratio"><small>ROCE</small><b>${pick("ROCE")}</b></div>
        <div class="ratio"><small>ROE</small><b>${pick("ROE")}</b></div>
        <div class="ratio"><small>Div yield</small><b>${pick("Dividend Yield")}</b></div>
        <div class="ratio"><small>Debt / Equity</small><b>${bs?.debt_to_equity ?? "–"}</b></div>
      </div>
      ${n.about ? `<p class="about" onclick="this.classList.toggle('open')">${esc(n.about)}</p>` : ""}`;

    html += `<h2>Promised vs delivered ★</h2><section class="card" id="pvd"><div class="reading"><span class="spinner"></span>Reading the last 4 concall transcripts…</div>${skeleton(3)}</section>`;

    if (n.latest_quarter) html += `<h2>Latest quarter</h2><section class="card">${resultsGrid(n.latest_quarter)}</section>`;

    if (t?.years?.length) {
      html += `<h2>Trajectory</h2><section class="card">
        <ul class="signals">${(t.signals || []).map((s) => `<li><span class="arrow ${s.dir}">${s.dir === "up" ? "↑" : s.dir === "down" ? "↓" : "→"}</span>${esc(s.text)}</li>`).join("")}</ul>
        ${barChart({ labels: t.years, a: t.revenue, b: t.pat, line: t.margin, aName: "Revenue", bName: "Net profit", lineName: "Op. margin" })}
        <div class="kv">${t.revenue_cagr_3y != null ? `<span>Revenue CAGR 3y / 5y</span><b>${t.revenue_cagr_3y}% / ${t.revenue_cagr_5y ?? "–"}%</b>` : ""}
        ${t.pat_cagr_3y != null ? `<span>Profit CAGR 3y / 5y</span><b>${t.pat_cagr_3y}% / ${t.pat_cagr_5y ?? "–"}%</b>` : ""}</div></section>`;
    }
    if (cq?.years?.length) {
      html += `<h2>Cash quality</h2><section class="card">
        <div class="kv"><span>Cash from operations ÷ profit, last 5 years</span><b>${cq.cfo_to_pat_5y ?? "–"}%</b></div>
        ${barChart({ labels: cq.years, a: cq.cfo, b: cq.pat, aName: "Cash from operations", bName: "Net profit" })}
        ${cq.verdict ? `<p class="verdict-line">${esc(cq.verdict)}</p>` : ""}</section>`;
    }
    if (bs && (bs.borrowings != null || bs.ccc)) {
      const last = (a) => a && a.length ? a[a.length - 1] : null;
      html += `<h2>Balance sheet</h2><section class="card"><div class="kv">
        ${bs.borrowings != null ? `<span>Borrowings (${esc(bs.as_of)})</span><b>₹${inr(bs.borrowings)} Cr</b><span>Net worth</span><b>₹${inr(bs.net_worth)} Cr</b><span>Debt / equity</span><b>${bs.debt_to_equity}</b>` : ""}
        ${last(bs.ccc) != null ? `<span>Cash conversion cycle</span><b>${last(bs.ccc)} days</b>` : ""}
        ${last(bs.wc_days) != null ? `<span>Working capital days</span><b>${last(bs.wc_days)}</b>` : ""}
        ${last(bs.roce) != null ? `<span>ROCE (latest year)</span><b>${last(bs.roce)}%</b>` : ""}
        </div>${bs.debt_series ? barChart({ labels: bs.cols, a: bs.debt_series, aName: "Borrowings ₹ Cr", aColor: "var(--ink-3)" }) : ""}</section>`;
    }
    html += `<p class="foot">Numbers: <a href="${esc(n.source)}" target="_blank" rel="noopener">screener.in standardised financials</a> (₹ Cr, consolidated where available). Narrative: company concall transcripts filed with the exchanges. No news, broker notes or model memory.</p>`;
    document.getElementById("rbody").innerHTML = html;
  }

  async function loadNarrative(sym) {
    try {
      const nv = S.research[sym]?.narrative || await api(`/api/research/${encodeURIComponent(sym)}/narrative`);
      S.research[sym].narrative = nv;
      if (currentSym() === sym) drawNarrative(nv);
    } catch (e) {
      const el = document.getElementById("pvd");
      if (el) el.innerHTML = `<div class="err">Transcript analysis failed: ${esc(e.message)}</div>`;
    }
  }

  const VORDER = ["delivered", "partial", "missed", "pending", "unverifiable"];
  function citeChips(cites) {
    return (cites || []).map((c) => /^T\d+-\d+$/.test(c)
      ? `<button class="cite" data-chunk="${esc(c)}">${esc(c)}</button>`
      : /^G\d+$/.test(c) ? "" : `<span class="cite num-cite">${esc(c.replace(/^Q:/, "").replace(/^A:/, "FY "))}</span>`).join("");
  }
  function drawNarrative(nv) {
    const el = document.getElementById("pvd");
    if (!el) return;
    if (!nv.available) { el.innerHTML = `<p class="card-sub">${esc(nv.reason)}</p>`; return; }
    const g = nv.guidance || [], total = g.length || 1;
    const tdates = nv.transcripts.map((t) => t.date).join(", ");
    let html = nv.partial ? `<p class="card-sub" style="color:var(--know)">Some calls couldn't be analysed right now (free-tier model limits). Showing what was verified; reopen later for the full read.</p>` : "";
    html += `<p class="card-sub">From ${nv.transcripts.length} calls (${esc(tdates)}), FINN pulled every quantified promise and checked it against reported numbers.</p>`;
    if (g.length) {
      html += `<div class="tally">${VORDER.map((v) => nv.tally[v] ? `<i class="v-${v}" style="width:${nv.tally[v] / total * 100}%"></i>` : "").join("")}</div>
        <div class="tally-legend">${VORDER.filter((v) => nv.tally[v]).map((v) => `<span class="v-${v}">${nv.tally[v]} ${v}</span>`).join("")}</div>`;
    }
    if (nv.credibility) html += `<p class="cred">${esc(nv.credibility)}</p>`;
    const ordered = [...g].sort((a, b) => VORDER.indexOf(a.verdict) - VORDER.indexOf(b.verdict));
    const testable = ordered.filter((x) => x.verdict !== "unverifiable");
    const untestable = ordered.filter((x) => x.verdict === "unverifiable");
    const promise = (x) => `<div class="promise">
        <div class="top-line"><span class="vbadge v-${esc(x.verdict)}">${esc(x.verdict)}</span><div>
          <div class="stmt">${esc(x.statement)}</div>
          <div class="meta">${esc(x.metric || "")}${x.target ? ` · target ${esc(x.target)}` : ""}${x.timeframe ? ` · ${esc(x.timeframe)}` : ""} · said on ${esc(x.call_date)} call</div>
          ${x.evidence ? `<div class="ev">${esc(x.evidence)}</div>` : ""}
          <blockquote>“${esc(x.quote)}”</blockquote>
          <div>${citeChips([x.chunk, ...(x.cites || [])].filter((c, i, a) => a.indexOf(c) === i))}</div>
        </div></div></div>`;
    html += testable.map(promise).join("");
    if (untestable.length) {
      html += `<details style="margin-top:6px"><summary class="card-sub" style="cursor:pointer">${untestable.length} more statement${untestable.length > 1 ? "s" : ""} the reported numbers can't test yet</summary>${untestable.map(promise).join("")}</details>`;
    }
    if (!g.length) html += `<p class="card-sub">No quantified guidance found in these calls.</p>`;
    el.innerHTML = html;

    if ((nv.bull || []).length || (nv.bear || []).length) {
      const bb = document.createElement("div");
      bb.innerHTML = `<h2>Bull vs bear</h2><section class="card bb">
        <div class="col"><h3><span class="arrow up">↑</span>Bull case</h3><ul>${nv.bull.map((p) => `<li>${esc(p.point)}<div>${citeChips(p.cites)}</div></li>`).join("") || "<li class='muted'>No cited bull points.</li>"}</ul></div>
        <div class="col"><h3><span class="arrow down">↓</span>Bear case</h3><ul>${nv.bear.map((p) => `<li>${esc(p.point)}<div>${citeChips(p.cites)}</div></li>`).join("") || "<li class='muted'>No cited bear points.</li>"}</ul></div></section>`;
      el.after(...bb.childNodes);
    }
    document.querySelectorAll("[data-chunk]").forEach((b) => b.onclick = () => {
      const c = nv.chunks[b.dataset.chunk]; if (!c) return;
      openSheet(`<h3>Source · ${esc(b.dataset.chunk)}</h3><p class="card-sub">Concall transcript, ${esc(c.date)}</p>
        <div class="chunk-text">${esc(c.text)}</div><p><a class="btn" href="${esc(c.url)}" target="_blank" rel="noopener">Open transcript PDF ↗</a></p>`);
    });
  }

  // ------------------------------------------------------------ settings sheet
  async function openSettings() {
    const cats = S.cats || S.config.default_alert_categories;
    let stats = null; try { stats = await api("/api/stats"); } catch { }
    openSheet(`<h3>Settings</h3><p class="card-sub">Saved on this device.</p>
      <div class="field"><label>Portfolio <span class="muted" style="font-weight:400">NSE symbols, comma-separated</span></label>
        <textarea id="s-port">${esc((S.portfolio || S.config.portfolio).join(", "))}</textarea></div>
      <div class="field"><label>Price move alert <b id="s-pv">±${S.price}%</b></label><input type="range" id="s-price" min="1" max="15" step="0.5" value="${S.price}"></div>
      <div class="field"><label>Volume spike alert <b id="s-vv">${S.volume}×</b></label><input type="range" id="s-vol" min="1.2" max="6" step="0.1" value="${S.volume}"></div>
      <div class="field"><label>Alert me on these filing types</label><div class="checks">
        ${S.config.categories.filter((c) => c !== "unclassified").map((c) => `<label><input type="checkbox" value="${esc(c)}" ${cats.includes(c) ? "checked" : ""}>${esc(catLabel(c))}</label>`).join("")}</div></div>
      <div style="display:flex;gap:8px;margin-top:18px"><button class="btn primary" id="s-save">Save</button><button class="btn" id="s-reset">Reset defaults</button></div>
      ${stats ? `<h2>Model usage (this server, since start)</h2><div class="stats">
        <div><b>${stats.cheap_calls}</b>small-model calls</div><div><b>${stats.strong_calls}</b>large-model calls</div>
        <div><b>${stats.cache_hits}</b>cache hits (free)</div><div><b>${inr(stats.prompt_tokens + stats.completion_tokens)}</b>tokens used</div></div>
        <p class="foot">Rules classify most filings at zero cost; a small model writes one-liners and extracts guidance; the large model is used once per company for verdicts.</p>` : ""}`);
    const pr = document.getElementById("s-price"), vo = document.getElementById("s-vol");
    pr.oninput = () => document.getElementById("s-pv").textContent = `±${pr.value}%`;
    vo.oninput = () => document.getElementById("s-vv").textContent = `${vo.value}×`;
    document.getElementById("s-save").onclick = () => {
      const port = document.getElementById("s-port").value.toUpperCase().split(/[\s,]+/).filter((x) => /^[A-Z0-9&-]{1,20}$/.test(x));
      S.portfolio = port.length ? port.slice(0, 25) : null;
      S.price = +pr.value; S.volume = +vo.value;
      S.cats = [...document.querySelectorAll(".checks input:checked")].map((i) => i.value);
      store.set("portfolio", S.portfolio); store.set("price", S.price); store.set("volume", S.volume); store.set("cats", S.cats);
      S.digest = null; S.alerts = null; closeSheet(); route();
    };
    document.getElementById("s-reset").onclick = () => {
      ["portfolio", "price", "volume", "cats"].forEach((k) => { try { localStorage.removeItem("finn." + k); } catch { } });
      Object.assign(S, { portfolio: null, price: 5, volume: 2, cats: null, digest: null, alerts: null });
      closeSheet(); route();
    };
  }

  function openSheet(html) {
    document.getElementById("sheet-body").innerHTML = html;
    document.getElementById("sheet").hidden = false;
    document.getElementById("sheet-backdrop").hidden = false;
  }
  function closeSheet() {
    document.getElementById("sheet").hidden = true;
    document.getElementById("sheet-backdrop").hidden = true;
  }
  document.getElementById("sheet-backdrop").onclick = closeSheet;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
  document.getElementById("open-settings").onclick = openSettings;

  // ------------------------------------------------------------ misc
  function skeleton(n) { return Array.from({ length: n }, (_, i) => `<div class="skeleton" style="width:${70 + (i * 37) % 30}%"></div>`).join(""); }
  function setBadge(tab, n) { const b = document.getElementById("badge-" + tab); if (!b) return; b.hidden = !n; b.textContent = n; }

  function route() {
    const [tab, arg] = (location.hash.slice(1) || "brief").split("/");
    document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
    window.scrollTo(0, 0);
    if (tab === "alerts") renderAlerts();
    else if (tab === "research") renderResearch(arg ? decodeURIComponent(arg).toUpperCase() : null);
    else renderBrief();
  }

  async function boot() {
    document.getElementById("today").textContent = new Date().toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
    try { S.config = await api("/api/config"); }
    catch (e) { $view.innerHTML = `<div class="err" style="margin-top:20px">Server is waking up (free tier sleeps when idle). Retrying…</div>`; setTimeout(boot, 4000); return; }
    window.addEventListener("hashchange", route);
    route();
    // warm alerts in the background so the badge is live
    const cats = S.cats || S.config.default_alert_categories;
    api(`/api/alerts?symbols=${symbolsParam()}&price=${S.price}&volume=${S.volume}&cats=${encodeURIComponent(cats.join(","))}`)
      .then((a) => { S.alerts = a; setBadge("alerts", a.alerts.filter((x) => x.severity === "act").length); }).catch(() => { });
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { });
  }
  boot();
})();
