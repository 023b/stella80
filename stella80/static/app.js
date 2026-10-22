"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
let toastT;
function toast(msg, ms = 4000) { const t = document.getElementById("toast"); t.textContent = msg; t.hidden = false; clearTimeout(toastT); if (ms) toastT = setTimeout(() => t.hidden = true, ms); }
const apiErr = async r => { const t = await r.text(); try { return JSON.parse(t).detail || t; } catch { return t; } };

const SUBJ_COLORS = ["#c8561e", "#e0a526", "#7a8b2c", "#3f7f8c", "#8c4a6b", "#5b3a1e"];
const SHORT = {};   // optional short labels for module codes, for example { "COMP3010": "VISION" }
const isCode = id => /^[A-Z]{3,4}M?\d{3,4}$/.test(id || "");

const store = {
  get(k, d) { try { const v = localStorage.getItem("s80." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("s80." + k, JSON.stringify(v)); } catch { } },
};

const S = {
  cfg: null,
  subject: store.get("subject", "ALL"),
  model: store.get("model", "scholar"),
  mode: store.get("mode", "teach"),
  depth: store.get("depth", "normal"),
  theme: store.get("theme", "oled"),
  me: null,
  think: store.get("think", false),
  vis: store.get("vis", true),
  strict: store.get("strict", false),
  chatId: null,
  messages: [],      // {role, content, thinking, sources, meta, images}
  busy: null,        // AbortController while streaming
  attach: [],        // data URLs
  status: null,
};

/* ================================================================ markdown + math */

marked.use({ gfm: true, breaks: false });
const renderer = new marked.Renderer();
renderer.code = (code, lang) => {
  lang = (lang || "").trim().toLowerCase();
  if (["plot", "mermaid", "cards"].includes(lang))
    return `<div class="vis pending" data-kind="${lang}" data-src="${esc(encodeURIComponent(code))}">[ ${lang === "cards" ? "shuffling cards" : "drawing " + lang} ... ]</div>`;
  return `<pre><button class="copy" type="button">COPY</button><code class="lang-${esc(lang)}">${esc(code)}</code></pre>`;
};

function protectMath(text) {
  const math = [];
  const put = (tex, display) => { math.push({ tex, display }); return `\uE000M${math.length - 1}\uE001`; };
  const parts = text.split(/(```[\s\S]*?(?:```|$)|`[^`\n]+`)/g);
  const out = parts.map((p, i) => {
    if (i % 2) return p;   // code: leave untouched
    return p
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => put(t, true))
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => put(t, true))
      .replace(/\\\(([\s\S]+?)\\\)/g, (_, t) => put(t, false))
      .replace(/(^|[^\\$\w])\$(?![\s$])([^$\n]{0,300}?[^\s\\$])\$(?![\w$])/g, (_, pre, t) => pre + put(t, false))
      .replace(/\[(\d{1,2}(?:\s*[,–-]\s*\d{1,2})*)\](?!\()/g, (_, n) => `\uE000C${n}\uE001`);
  }).join("");
  return { out, math };
}

function renderMath(tex, display) {
  try { return katex.renderToString(tex, { displayMode: display, throwOnError: false, output: "html", strict: "ignore" }); }
  catch { return esc(tex); }
}

function md(text, { wikilinks = false } = {}) {
  let src = text || "";
  if (wikilinks) {
    src = src.replace(/!\[\[([^\]|]+?\.(?:png|jpe?g|gif|webp|svg))(?:\|[^\]]*)?\]\]/gi, (_, p) => `<img src="vault/${encodeURI(p.trim())}" alt="">`)
      .replace(/!\[([^\]]*)\]\(([^)\s]+?\.(?:png|jpe?g|gif|webp))\)/gi, "")   // relative pptx images we don't host
      .replace(/\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]/g, (_, p, alias) => `<span class="wl" data-path="${esc(p.trim())}">${esc(alias || p.split("/").pop())}</span>`);
  }
  const { out, math } = protectMath(src);
  let html = marked.parse(out, { renderer });
  html = DOMPurify.sanitize(html, { ADD_TAGS: ["details", "summary"], ADD_ATTR: ["data-kind", "data-src", "data-path", "open"] });
  return html
    .replace(/\uE000M(\d+)\uE001/g, (_, i) => renderMath(math[i].tex, math[i].display))
    .replace(/\uE000C([^\uE001]+)\uE001/g, (_, n) => {
      const nums = n.split(/\s*,\s*/).flatMap(x => {
        const r = x.split(/\s*[–-]\s*/).map(Number);
        return r.length === 2 && r[1] - r[0] < 10 ? Array.from({ length: r[1] - r[0] + 1 }, (_, k) => r[0] + k) : [r[0]];
      });
      return nums.map(x => `<span class="cite" data-n="${x}">${x}</span>`).join("");
    });
}

/* ================================================================ visual blocks */

function looseJSON(s) {
  try { return JSON.parse(s); } catch { }
  try { return JSON.parse(s.replace(/,\s*([}\]])/g, "$1").replace(/([{,]\s*)([A-Za-z_]\w*)\s*:/g, '$1"$2":').replace(/'/g, '"')); } catch (e) {
    // a small model sometimes breaks the data points; the curves are what matter, so read those fields on their own
    const spec = {}, head = s.replace(/"(?:points|series)"[\s\S]*$/, ""), grab = k => { const m = new RegExp('"' + k + '"\\s*:\\s*(\\[[^\\[\\]{}]*\\]|"[^"]*")').exec(head); if (!m) return; try { return JSON.parse(m[1]); } catch { } };
    for (const k of ["title", "x", "y", "fns", "labels", "surface"]) { const v = grab(k); if (v !== undefined) spec[k] = v; }
    if (!spec.fns && !spec.surface) throw e;
    return spec;
  }
}

try { math.import({ ln: Math.log, sigmoid: x => 1 / (1 + Math.exp(-x)), relu: x => Math.max(0, x) }, { override: false }); } catch { }

function plotColors() {
  return [cssVar("--scr-fg"), cssVar("--scr-hi"), "#e0703a", "#a3b443", "#6fb3c4", "#d58ab0"];
}

function drawPlot(el, spec) {
  if (!window.Plotly) { setTimeout(() => drawPlot(el, spec), 300); return; }
  const fg = cssVar("--scr-fg"), faint = cssVar("--scr-faint"), dim = cssVar("--scr-dim");
  const colors = plotColors();
  const traces = [];
  const xr = spec.x || spec.xrange || [-5, 5];
  if (spec.surface) {
    const yr = spec.y || xr, f = math.compile(String(spec.surface)), N = 60;
    const xs = Array.from({ length: N }, (_, i) => xr[0] + (xr[1] - xr[0]) * i / (N - 1));
    const ys = Array.from({ length: N }, (_, i) => yr[0] + (yr[1] - yr[0]) * i / (N - 1));
    const z = ys.map(y => xs.map(x => { try { const v = f.evaluate({ x, y }); return isFinite(v) ? v : null; } catch { return null; } }));
    traces.push({ type: "surface", x: xs, y: ys, z, showscale: false,
      colorscale: [[0, cssVar("--scr-bg2") || "#222"], [0.5, dim], [1, cssVar("--scr-hi")]] });
  }
  const fns = [].concat(spec.fns || spec.functions || spec.fn || []);
  fns.forEach((expr, i) => {
    const e = typeof expr === "object" ? expr.expr : expr;
    const f = math.compile(String(e).replace(/^\s*(?:y|f\(\w\)|\w+\(\w\))\s*=\s*/, "").replace(/\*\*/g, "^"));
    const N = 500, xs = [], ys = [];
    for (let k = 0; k < N; k++) {
      const x = xr[0] + (xr[1] - xr[0]) * k / (N - 1);
      let y; try { y = f.evaluate({ x, t: x, theta: x }); if (typeof y === "object") y = y.re ?? null; } catch { y = null; }
      xs.push(x); ys.push(isFinite(y) && Math.abs(y) < 1e9 ? y : null);
    }
    traces.push({ x: xs, y: ys, mode: "lines", name: (spec.labels || [])[i] || (typeof expr === "object" && expr.label) || e,
      line: { color: colors[i % colors.length], width: 2.2 } });
  });
  [].concat(spec.points || spec.series || []).forEach((p, i) => {
    traces.push({ x: p.x, y: p.y, mode: p.mode || "markers+lines", name: p.label || `data ${i + 1}`,
      marker: { size: 6, color: colors[(fns.length + i) % colors.length] }, line: { width: 1.5, color: colors[(fns.length + i) % colors.length] } });
  });
  if (!traces.length) throw new Error("plot has no fns, points or surface");
  const axis = { gridcolor: faint, zerolinecolor: dim, linecolor: dim, tickfont: { color: dim }, title: { font: { color: dim } } };
  const layout = {
    height: spec.surface ? 420 : 340, autosize: true,
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "Plex, monospace", color: fg, size: 12 },
    margin: { l: 48, r: 14, t: 10, b: 38 }, showlegend: traces.length > 1,
    legend: { x: 1, xanchor: "right", y: 1, bgcolor: "rgba(0,0,0,0)", font: { color: fg } },
    xaxis: { ...axis, title: { text: spec.xlabel || "", font: { color: dim } } },
    yaxis: { ...axis, title: { text: spec.ylabel || "", font: { color: dim } }, ...(spec.y && !spec.surface ? { range: spec.y } : {}) },
    scene: { xaxis: axis, yaxis: axis, zaxis: axis, bgcolor: "rgba(0,0,0,0)" },
  };
  Plotly.newPlot(el, traces, layout, { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["select2d", "lasso2d", "toImage"] });
}

let mermaidReady = false, mermaidN = 0;
function initMermaid() {
  if (!window.mermaid) return false;
  const paper = S.theme === "paper";
  mermaid.initialize({
    startOnLoad: false, securityLevel: "strict", theme: "base",
    fontFamily: "Plex, monospace",
    themeVariables: {
      background: "transparent", fontSize: "13px",
      primaryColor: paper ? "#efe4cb" : cssVar("--scr-code"), primaryTextColor: cssVar("--scr-fg"),
      primaryBorderColor: cssVar("--scr-dim"), lineColor: cssVar("--scr-dim"), textColor: cssVar("--scr-fg"),
      secondaryColor: paper ? "#f6dcc4" : cssVar("--scr-bg2"), tertiaryColor: paper ? "#f4ecd8" : cssVar("--scr-bg"),
      noteBkgColor: cssVar("--scr-code"), noteTextColor: cssVar("--scr-fg"), actorBorder: cssVar("--scr-dim"),
      actorBkg: cssVar("--scr-code"), actorTextColor: cssVar("--scr-fg"), signalColor: cssVar("--scr-fg"), signalTextColor: cssVar("--scr-fg"),
    },
  });
  return (mermaidReady = true);
}

// A small model often writes labels Mermaid will not parse (unquoted brackets, "Note over" in a flowchart). Try the diagram as written,
// then with such labels repaired, then with brackets removed from labels, and draw the first version that parses.
function mermaidVariants(code) {
  const base = code.replace(/^\s*Note (?:over|left of|right of) .*$/gim, "");
  const quoted = base.replace(/(\b[A-Za-z_]\w*)\[([^\]"\n]*[()][^\]"\n]*)\]/g, '$1["$2"]');
  const plain = base.replace(/\[([^\]"\n]*)\]/g, (m, t) => "[" + t.replace(/[()]/g, " ") + "]");
  return [...new Set([base, quoted, plain])];
}

async function drawMermaid(el, code) {
  if (!mermaidReady && !initMermaid()) { setTimeout(() => drawMermaid(el, code), 300); return; }
  const id = "mm" + (++mermaidN), variants = mermaidVariants(code);
  let use = variants[0];
  for (const v of variants) { try { if (await mermaid.parse(v, { suppressErrors: true })) { use = v; break; } } catch { } }
  try {
    const { svg } = await mermaid.render(id, use);
    el.innerHTML = svg;
  } catch (e) {
    document.getElementById("d" + id)?.remove();   // mermaid leaves a "Syntax error" graphic in the page when it fails
    throw e;
  }
}

function drawCards(el, code) {
  const cards = [];
  let cur = null;
  code.split("\n").forEach(line => {
    const m = line.match(/^\s*(Q|A)\s*[:.]\s*(.*)$/i);
    if (m && m[1].toUpperCase() === "Q") { cur = { q: m[2], a: "" }; cards.push(cur); }
    else if (m && cur) cur.a = m[2];
    else if (cur && line.trim()) cur[cur.a ? "a" : "q"] += " " + line.trim();
  });
  if (!cards.length) throw new Error("no Q:/A: lines");
  el.className = "";
  el.innerHTML = `<div class="cards-bar">${cards.length} CARDS · CLICK TO FLIP · <button class="lnk" data-act="flipall">FLIP ALL</button></div>
    <div class="cards">${cards.map((c, i) => `<div class="card"><div class="card-in">
      <div class="card-face"><span class="lbl">CARD ${i + 1} / Q</span>${md(c.q)}</div>
      <div class="card-face back"><span class="lbl">ANSWER</span>${md(c.a)}</div></div></div>`).join("")}</div>`;
  $(".lnk", el).style.cssText = "background:none;border:0;color:inherit;font:inherit;text-decoration:underline;cursor:pointer";
}

function hydrateVisuals(root) {
  $$(".vis.pending", root).forEach(el => {
    const kind = el.dataset.kind, code = decodeURIComponent(el.dataset.src);
    el.classList.remove("pending");
    el.textContent = "";
    try {
      if (kind === "cards") return drawCards(el, code);
      let title = kind === "mermaid" ? "DIAGRAM" : "PLOT", spec;
      if (kind === "plot") { spec = looseJSON(code); if (spec.title) title = spec.title; }
      el.innerHTML = `<div class="vis-title"><span>${esc(String(title).toUpperCase())}</span><button type="button" data-act="src">SOURCE</button></div>
        <div class="${kind === "plot" ? "plot" : "mermaid-out"}"></div><pre hidden><code>${esc(code)}</code></pre>`;
      const target = el.children[1];
      if (kind === "plot") drawPlot(target, spec);
      else drawMermaid(target, code).catch(e => { target.innerHTML = `<div class="vis-err">diagram didn't parse: ${esc(e.message || e).slice(0, 160)}</div>`; $("pre", el).hidden = false; });
    } catch (e) {
      el.innerHTML = `<div class="vis-err">couldn't draw this ${kind}: ${esc(e.message).slice(0, 160)}</div><pre><code>${esc(code)}</code></pre>`;
    }
  });
}

/* ================================================================ log rendering */

const log = $("#log");
const nearBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 120;
const toBottom = (force) => { if (force || nearBottom()) log.scrollTop = log.scrollHeight; };

const subjTag = id => { const sj = subjOf(id); return (sj && !isCode(sj.id)) ? (sj.code || sj.name.slice(0, 4).toUpperCase()) : subjectLabel(id).slice(0, 4); };
const subjOf = id => (S.cfg?.subjects || []).find(x => x.id === id);
function subjectLabel(id) {
  const sj = subjOf(id);
  if (sj && !isCode(sj.id)) return sj.name.toUpperCase();   // subjects without a module code read as their full names
  return id === "ALL" || !id ? "ALL" : (SHORT[id] || (/^[A-Z]{3,4}M?\d{3,4}$/.test(id) ? id : id.replace(/-/g, " ").toUpperCase().slice(0, 26))); }

function userEl(m) {
  const d = document.createElement("div");
  d.className = "msg msg-user";
  const meta = m.meta || {};
  d.innerHTML = `<div class="who">YOU ${meta.mode ? `<span class="chip">${esc(meta.mode.toUpperCase())}</span>` : ""}${meta.subject && meta.subject !== "ALL" ? `<span class="chip">${esc(subjectLabel(meta.subject))}</span>` : ""}</div>
    <div class="body-text">${esc(m.content)}</div>
    ${(m.images || []).length ? `<div class="imgs">${m.images.map(s => `<img src="${s}" alt="attached">`).join("")}</div>` : ""}`;
  return d;
}

function botEl(m, idx) {
  const d = document.createElement("div");
  d.className = "msg msg-bot";
  d.dataset.idx = idx;
  d.innerHTML = `<div class="who">STELLA <span class="chip mdl">${esc((m.meta || {}).model || "")}</span></div>
    <div class="notices"></div>
    <details class="thinking" ${m.thinking ? "" : "hidden"}><summary>REASONING TAPE</summary><div class="tk"></div></details>
    <div class="md answer"></div><div class="meta"></div>`;
  fillBot(d, m, true);
  return d;
}

function fillBot(d, m, final) {
  $(".mdl", d).textContent = (m.meta || {}).model || "";
  $(".mdl", d).hidden = !(m.meta || {}).model;
  $(".notices", d).innerHTML = (m.notices || []).map(n => `<div class="notice ${n.err ? "err" : ""}">${esc(n.text)}</div>`).join("");
  if (m.thinking) { $(".thinking", d).hidden = false; $(".tk", d).textContent = m.thinking; }
  const ans = $(".answer", d);
  if (m.content) ans.innerHTML = md(final ? fenceCards(m.content) : m.content);
  else if (!final) ans.innerHTML = `<span class="status-line">${esc(m.phase || "")}</span>`;
  ans.classList.toggle("cursor", !final);
  if (final) {
    hydrateVisuals(ans);
    const st = m.meta || {};
    const bits = [];
    if (st.tps) bits.push(`${st.tps} TOK/S`);
    if (st.total_s) bits.push(`${st.total_s}S`);
    if (m.sources && m.sources.length) bits.push(`<button data-act="srcs">${m.sources.length} SOURCES</button>`);
    if (m.content) bits.push(`<button data-act="copy">COPY</button>`);
    $(".meta", d).innerHTML = bits.map(b => `<span>${b}</span>`).join("");
  }
}

// Small models sometimes drop the ```cards fence and just write Q:/A: lines; fence them back up.
function fenceCards(text) {
  if (/```cards/.test(text) || (text.match(/^\s*Q\s*:/gm) || []).length < 2) return text;
  const lines = text.replace(/^\s*cards\s*\n/i, "").split("\n");
  const first = lines.findIndex(l => /^\s*Q\s*:/.test(l));
  let last = lines.length - 1;
  while (last > first && !/^\s*A\s*:/.test(lines[last])) last--;
  return [...lines.slice(0, first), "```cards", ...lines.slice(first, last + 1), "```", ...lines.slice(last + 1)].join("\n");
}

function renderLog() {
  log.innerHTML = "";
  if (!S.messages.length) return renderWelcome();
  S.messages.forEach((m, i) => log.appendChild(m.role === "user" ? userEl(m) : botEl(m, i)));
  const lastBot = [...S.messages].reverse().find(m => m.role === "assistant");
  showSources(lastBot ? lastBot.sources : []);
  toBottom(true);
}

function topicItems() {
  const subs = (S.cfg?.subjects || []).filter(s => S.subject === "ALL" || s.id === S.subject);
  const items = [];
  subs.forEach(s => s.topics.forEach(t => t.split(/\s*[,;:]\s*|\s+and\s+(?=[A-Z])/).forEach(x => {
    x = x.replace(/\(.*?\)/g, "").trim();
    if (x.length > 3 && x.length < 60) items.push(x);
  })));
  for (let i = items.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [items[i], items[j]] = [items[j], items[i]]; }
  return [...new Set(items)].slice(0, 8);
}

function ideaItems() {
  const subs = (S.cfg?.subjects || []).filter(s => S.subject === "ALL" || s.id === S.subject);
  const all = [];
  subs.forEach(s => (s.ideas || []).forEach((x, i) => all.push({ ...x, rank: i })));
  all.sort((p, q) => (q.fresh - p.fresh) || (p.rank - q.rank));
  return all.slice(0, 6);
}

const PHRASE = {
  teach: t => `Teach me ${t} from scratch`, exam: t => `Write an exam answer on ${t}`, derive: t => `Derive the maths behind ${t}`,
  quiz: t => `Quiz me on ${t}`, cards: t => `Flashcards for ${t}`, code: t => `Show me ${t} in code`,
  simple: t => `${t} in plain words`, brief: t => `${t}, briefly`,
};

function renderWelcome() {
  const st = S.status || {};
  const model = (S.cfg?.models || []).find(m => m.id === S.model);
  const subj = S.subject === "ALL" ? "ALL MODULES" : `${subjOf(S.subject)?.code || S.subject} ${subjOf(S.subject)?.name || ""}`.toUpperCase();
  const w = document.createElement("div");
  w.className = "welcome";
  w.innerHTML = `<div class="boot">STELLA-80 STUDY BASIC  V1.0
(C) 1980 STELLA SYSTEMS · GUILDFORD
USER     <span class="hi">${esc((S.me?.display || "").toUpperCase())}</span>  ${esc(S.me?.vault_title ? "(" + S.me.vault_title + ")" : "")}

${st.files ?? "..."} NOTES · ${(st.chunks ?? 0).toLocaleString()} BLOCKS ON DISK
MODULE   <span class="hi">${esc(subj)}</span>
ENGINE   <span class="hi">${esc(model ? model.label : "?")}</span>  ${esc(model ? "(" + model.model + ")" : "")}
MODE     <span class="hi">${esc(S.mode.toUpperCase())}</span>
RAM FREE ${st.mem_free ? (st.mem_free / 1e9).toFixed(1) + "G" : "..."}

READY.<span class="cursor"></span></div>
    <div class="try-label">TRY:</div><div class="try">${(() => {
      const ideas = ideaItems();
      const fill = topicItems().slice(0, Math.max(0, 8 - ideas.length));
      return ideas.map(x => `<button type="button" class="idea" data-q="${esc(x.q)}" title="From the file: ${esc(x.src)}">${x.fresh ? '<b class="new">NEW</b> ' : ""}${esc(x.q)}</button>`).join("")
        + fill.map(t => `<button type="button" data-q="${esc(PHRASE[S.mode](t))}">${esc(PHRASE[S.mode](t))}</button>`).join("");
    })()}</div>`;
  log.appendChild(w);
  showSources([]);
}

/* ================================================================ sources tray */

let shownSources = [];
function showSources(list, flashN) {
  shownSources = list || [];
  $(".body").classList.toggle("src-hidden", !shownSources.length);
  $("#src-count").textContent = shownSources.length ? shownSources.length : "";
  const el = $("#src-list");
  if (!shownSources.length) { el.innerHTML = `<p class="muted">Sources from your vault appear here when you ask something.</p>`; return; }
  el.innerHTML = `<div class="legend"><span><i style="background:var(--rust)"></i>MY NOTES</span><span><i style="background:var(--mustard)"></i>PROCESSED</span><span><i style="background:var(--avocado)"></i>RAW LECTURE</span></div>` +
    shownSources.map(s => `<div class="src k-${esc(s.kind)}" data-path="${esc(s.path)}" data-heading="${esc(s.heading || "")}" data-n="${s.n}" role="button" tabindex="0">
      <span class="kind">${esc(subjectLabel(s.subject.split(",")[0]))}</span>
      <span class="n">[${s.n}]</span><span class="st">${esc(s.title)}</span>
      ${s.heading ? `<span class="sh">&gt; ${esc(s.heading)}</span>` : ""}
      <span class="sn">${esc(s.snippet.replace(/[#*`>|]/g, "").replace(/!\[\[[^\]]+\]\]/g, "[slide]"))}</span>
      ${s.images && s.images.length ? `<span class="slides">${s.images.map(p => `<img loading="lazy" src="vault/${encodeURI(p)}" data-full="vault/${encodeURI(p)}" alt="slide">`).join("")}</span>` : ""}
    </div>`).join("");
  if (flashN) {
    const c = $(`.src[data-n="${flashN}"]`, el);
    if (c) { c.scrollIntoView({ block: "center", behavior: "smooth" }); c.classList.add("flash"); setTimeout(() => c.classList.remove("flash"), 1300); }
    if (innerWidth <= 1020) $("#sources").classList.add("open");
  }
}

/* ================================================================ printout (note viewer) */

async function openNote(path, heading) {
  const r = await fetch("api/note?path=" + encodeURIComponent(path));
  if (!r.ok) return;
  const n = await r.json();
  $("#po-path").textContent = n.path.replace(/^[^/]+\//, "").replace(/\.md$/, "");
  const body = $("#po-body");
  body.innerHTML = md(n.text, { wikilinks: true });
  body.dataset.title = n.title;
  hydrateVisuals(body);
  $("#printout").hidden = false;
  $("#printout").scrollTop = 0;
  if (heading) {
    const h = $$("h1,h2,h3,h4", body).find(x => x.textContent.trim().toLowerCase() === heading.trim().toLowerCase());
    if (h) setTimeout(() => h.scrollIntoView({ block: "start" }), 50);
  }
}

/* ================================================================ controls */

function renderControls() {
  const subs = S.cfg.subjects;
  $("#subjects").innerHTML = [{ id: "ALL", name: "Everything", notes: null }, ...subs].map((s, i) =>
    `<button type="button" class="cart ${S.subject === s.id ? "on" : ""}" data-v="${s.id}" style="--cc:${i ? SUBJ_COLORS[(i - 1) % SUBJ_COLORS.length] : "#a8987a"}">
      ${esc(subjectLabel(s.id))}<small>${esc(s.id === "ALL" ? "all modules" : (s.code || (isCode(s.id) ? s.id : "")))}</small></button>`).join("");
  $("#models").innerHTML = S.cfg.models.map(m =>
    `<button type="button" data-v="${m.id}" title="${esc(m.blurb)}${m.installed ? "" : " (not installed)"}" class="${S.model === m.id ? "on" : ""} ${m.online && m.installed ? "" : "off"} ${m.loaded ? "loaded" : ""}"><b></b>${esc(m.label)}</button>`).join("");
  const idx = Math.max(0, S.cfg.models.findIndex(m => m.id === S.model));
  $(".knob-cap").style.transform = `rotate(${(idx - (S.cfg.models.length - 1) / 2) * 50}deg)`;
  $("#modes").innerHTML = S.cfg.modes.map(m => `<button type="button" class="key ${S.mode === m.id ? "on" : ""}" data-v="${m.id}">${esc(m.label)}</button>`).join("");
  $$("#depth-seg button").forEach(b => b.classList.toggle("on", b.dataset.v === S.depth));
  $$("#theme-seg button").forEach(b => b.classList.toggle("on", b.dataset.v === S.theme));
  $("#sw-think").checked = S.think; $("#sw-vis").checked = S.vis; $("#sw-strict").checked = S.strict;
  const subj = subs.find(s => s.id === S.subject);
  $("#screen-subject").textContent = subj ? `${subj.code || subj.id} · ${subj.name.toUpperCase()}` : "ALL MODULES";
  const mdl = S.cfg.models.find(m => m.id === S.model);
  $("#screen-model").textContent = mdl ? `${mdl.label}${S.think ? " + THINK" : ""}` : "";
  $("#deck-summary").textContent = [subjectLabel(S.subject), mdl?.label, S.mode.toUpperCase(), S.think && "THINK", S.strict && "VAULT ONLY"].filter(Boolean).join(" · ");
  const lim = S.me?.limits;
  if (lim) {
    for (const [id, key] of [["sw-think", "think"], ["sw-vis", "visuals"]]) {
      const el = $("#" + id); el.disabled = !lim[key]; if (!lim[key]) { el.checked = false; S[key === "visuals" ? "vis" : key] = false; }
      el.closest(".rocker").classList.toggle("locked", !lim[key]); el.closest(".rocker").title = lim[key] ? el.closest(".rocker").title : "Turned off for your account by " + (S.me?.admin_word || "your admin");
    }
    const rank = ["brief", "normal", "deep"].indexOf(lim.max_depth);
    $$("#depth-seg button").forEach((b, i) => { b.disabled = i > rank; b.classList.toggle("locked", i > rank); });
    if (["brief", "normal", "deep"].indexOf(S.depth) > rank) S.depth = lim.max_depth;
    $("#file").closest("label").hidden = !lim.images;
    $("#unload").hidden = false;
  }
  document.documentElement.dataset.theme = S.theme;
}

function setOpt(k, v) {
  S[k] = v; store.set(k, v);
  renderControls();
  if (k === "theme") { mermaidReady = false; renderLog(); }
  else if (!S.messages.length && ["subject", "mode", "model"].includes(k)) renderLog();
}

$("#subjects").addEventListener("click", e => { const b = e.target.closest(".cart"); if (b) setOpt("subject", b.dataset.v); });
$("#models").addEventListener("click", e => { const b = e.target.closest("button"); if (b) setOpt("model", b.dataset.v); });
$("#modes").addEventListener("click", e => { const b = e.target.closest(".key"); if (b) setOpt("mode", b.dataset.v); });
$("#depth-seg").addEventListener("click", e => { const b = e.target.closest("button"); if (b) setOpt("depth", b.dataset.v); });
$("#sw-think").onchange = e => setOpt("think", e.target.checked);
$("#sw-vis").onchange = e => setOpt("vis", e.target.checked);
$("#sw-strict").onchange = e => setOpt("strict", e.target.checked);
const turnKnob = dir => {
  const ms = S.cfg.models, i = ms.findIndex(m => m.id === S.model);
  setOpt("model", ms[(i + dir + ms.length) % ms.length].id);
};
$("#knob").onclick = () => turnKnob(1);
$("#knob").onkeydown = e => { if (e.key === "ArrowRight" || e.key === "ArrowUp") { turnKnob(1); e.preventDefault(); } if (e.key === "ArrowLeft" || e.key === "ArrowDown") { turnKnob(-1); e.preventDefault(); } };

/* ================================================================ chats sidebar */

async function loadChats() {
  const q = $("#chat-search").value.trim();
  const chats = await (await fetch("api/chats" + (q ? "?q=" + encodeURIComponent(q) : ""))).json();
  const subs = (S.cfg?.subjects || []).map(s => s.id);
  const day = t => {
    const d = new Date(t * 1000), now = new Date();
    const diff = Math.floor((new Date(now.toDateString()) - new Date(d.toDateString())) / 864e5);
    return diff === 0 ? "TODAY" : diff === 1 ? "YESTERDAY" : diff < 7 ? d.toLocaleDateString(undefined, { weekday: "long" }).toUpperCase() : d.toLocaleDateString(undefined, { day: "numeric", month: "short" }).toUpperCase();
  };
  let last = null, html = "";
  chats.forEach(c => {
    const g = c.pinned ? "PINNED" : day(c.updated);
    if (g !== last) { html += `<div class="chat-day">${g}</div>`; last = g; }
    const si = subs.indexOf(c.subject);
    html += `<div class="chat-item ${c.id === S.chatId ? "active" : ""}" data-id="${c.id}" role="button" tabindex="0">
      <span class="t">${esc(c.title)}</span>
      <span class="x" data-act="delete" title="Delete this session">&#x2715;</span>
      <span class="tag" style="--tagc:${si >= 0 ? SUBJ_COLORS[si % SUBJ_COLORS.length] : "#8f7f60"}">${esc(subjTag(c.subject))}</span>
      <span class="acts"><span data-act="rename">RENAME</span><span data-act="pin">${c.pinned ? "UNPIN" : "PIN"}</span><span data-act="export">EXPORT .MD</span><span data-act="delete">DELETE</span></span>
    </div>`;
  });
  $("#chat-list").innerHTML = html || `<p class="muted" style="padding:0 4px">No sessions yet.</p>`;
}

async function openChat(id) {
  if (S.busy) return;
  const c = await (await fetch("api/chats/" + id)).json();
  S.chatId = id;
  S.messages = c.messages.map(m => ({ ...m, images: [] }));
  if (c.subject && c.subject !== S.subject) { S.subject = c.subject; store.set("subject", c.subject); renderControls(); }
  renderLog(); loadChats();
  $("#side").classList.remove("open");
}

function newChat() {
  if (S.busy) S.busy.abort();
  S.chatId = null; S.messages = []; S.attach = []; renderAttach();
  renderLog(); loadChats();
  $("#input").focus();
  $("#side").classList.remove("open");
}

$("#new-chat").onclick = newChat;
let searchT;
$("#chat-search").oninput = () => { clearTimeout(searchT); searchT = setTimeout(loadChats, 250); };
$("#chat-list").addEventListener("click", async e => {
  const item = e.target.closest(".chat-item"); if (!item) return;
  const id = item.dataset.id, act = e.target.dataset.act;
  if (!act) return openChat(id);
  if (act === "delete" && confirm("Delete this session?")) {
    await fetch("api/chats/" + id, { method: "DELETE" });
    if (S.chatId === id) newChat(); else loadChats();
  } else if (act === "rename") {
    const t = prompt("Session name:", $(".t", item).textContent);
    if (t) { await fetch("api/chats/" + id, { method: "PATCH", body: JSON.stringify({ title: t }) }); loadChats(); }
  } else if (act === "pin") {
    await fetch("api/chats/" + id, { method: "PATCH", body: JSON.stringify({ pinned: $("[data-act=pin]", item).textContent === "PIN" }) }); loadChats();
  } else if (act === "export") location.href = `api/chats/${id}/export`;
});

/* ================================================================ attachments */

function shrink(file) {
  return new Promise(res => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      const k = Math.min(1, 1400 / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url); res(c.toDataURL("image/jpeg", .88));
    };
    img.src = url;
  });
}
async function addFiles(files) {
  for (const f of files) if (f.type.startsWith("image/") && S.attach.length < 3) S.attach.push(await shrink(f));
  renderAttach();
}
function renderAttach() {
  $("#attach-strip").innerHTML = S.attach.map((s, i) => `<span class="th"><img src="${s}" alt=""><span data-i="${i}">&#x2715;</span></span>`).join("");
}
$("#file").onchange = e => { addFiles(e.target.files); e.target.value = ""; };
$("#attach-strip").onclick = e => { if (e.target.dataset.i) { S.attach.splice(+e.target.dataset.i, 1); renderAttach(); } };
$("#input").addEventListener("paste", e => {
  const files = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith("image/"));
  if (files.length) { e.preventDefault(); addFiles(files); }
});

/* ================================================================ ask */

const input = $("#input");
const autosize = () => { input.style.height = "auto"; input.style.height = Math.min(200, input.scrollHeight) + "px"; };
input.addEventListener("input", autosize);
input.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#ask").requestSubmit(); }
});

$("#ask").addEventListener("submit", async e => {
  e.preventDefault();
  if (S.busy) { S.busy.abort(); return; }
  const text = input.value.trim();
  if (!text) return;
  input.value = ""; autosize();
  await ask(text);
});

async function ask(text) {
  if (!S.messages.length) log.innerHTML = "";
  const images = S.attach.slice(); S.attach = []; renderAttach();
  const u = { role: "user", content: text, images, meta: { mode: S.mode, subject: S.subject } };
  S.messages.push(u); log.appendChild(userEl(u));
  const b = { role: "assistant", content: "", thinking: "", sources: [], meta: {}, notices: [], phase: "SEARCHING VAULT..." };
  S.messages.push(b);
  const el = botEl(b, S.messages.length - 1);
  fillBot(el, b, false);
  log.appendChild(el); toBottom(true);

  const ctrl = new AbortController(); S.busy = ctrl;
  const sendBtn = $("#send"); sendBtn.textContent = "STOP"; sendBtn.classList.add("busy");
  let pending = false, finished = false;
  const paint = () => { if (pending || finished) return; pending = true; requestAnimationFrame(() => { pending = false; if (finished) return; fillBot(el, b, false); toBottom(); }); };
  const t0 = performance.now();
  const tick = setInterval(() => {
    if (!b.content && !b.thinking && b.phase && b.phase.startsWith("WARMING")) { b.phase = b.phase.replace(/\(\d+S\)$/, "") + `(${Math.round((performance.now() - t0) / 1000)}S)`; paint(); }
  }, 1000);

  try {
    const r = await fetch("api/chat", {
      method: "POST", signal: ctrl.signal, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, chat_id: S.chatId, subject: S.subject, model: S.model, mode: S.mode, depth: S.depth,
        think: S.think, visuals: S.vis, grounding: S.strict ? "strict" : "blend", images }),
    });
    if (!r.ok) throw new Error(await apiErr(r));
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
        if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.type === "chat") { if (S.chatId !== ev.chat_id) { S.chatId = ev.chat_id; loadChats(); } }
        else if (ev.type === "sources") { b.sources = ev.sources; showSources(ev.sources); b.phase = `FOUND ${ev.sources.length} PASSAGES · WARMING ENGINE `; }
        else if (ev.type === "model") { b.meta.model = ev.label; }
        else if (ev.type === "queue") b.phase = ev.ahead ? `IN LINE: ${ev.ahead} AHEAD OF YOU. STELLA ANSWERS ONE AT A TIME...` : "YOUR TURN...";
        else if (ev.type === "notice") b.notices.push({ text: ev.text });
        else if (ev.type === "think") b.thinking += ev.text;
        else if (ev.type === "token") b.content += ev.text;
        else if (ev.type === "error") b.notices.push({ text: "ENGINE ERROR: " + ev.text, err: true });
        else if (ev.type === "done") b.meta = { ...b.meta, ...ev.stats, model: ev.model };
        paint();
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") b.notices.push({ text: "LINK ERROR: " + err.message, err: true });
    else b.notices.push({ text: "BREAK. (stopped)" });
  } finally {
    clearInterval(tick);
    finished = true;   // a repaint still queued must not overwrite the finished answer (it would bring back "[ drawing plot ... ]")
    S.busy = null; sendBtn.textContent = "RUN"; sendBtn.classList.remove("busy");
    if (b.thinking && b.content) $(".thinking", el).open = false;
    fillBot(el, b, true); toBottom();
    loadChats(); refreshStatus();
  }
}

/* ================================================================ global clicks / keys */

document.addEventListener("click", async e => {
  const t = e.target;
  if (t.closest(".try button")) { input.value = t.closest("button").dataset.q; autosize(); input.focus(); return; }
  if (t.classList.contains("cite")) {
    const msg = t.closest(".msg-bot");
    const m = msg ? S.messages[+msg.dataset.idx] : null;
    if (m && m.sources !== shownSources) showSources(m.sources, +t.dataset.n);
    else showSources(shownSources, +t.dataset.n);
    return;
  }
  if (t.dataset.act === "srcs") { const m = S.messages[+t.closest(".msg-bot").dataset.idx]; showSources(m.sources); if (innerWidth <= 1020) $("#sources").classList.add("open"); return; }
  if (t.dataset.act === "copy") { const m = S.messages[+t.closest(".msg-bot").dataset.idx]; navigator.clipboard?.writeText(m.content); t.textContent = "COPIED"; setTimeout(() => t.textContent = "COPY", 1200); return; }
  if (t.dataset.act === "src") { const pre = t.closest(".vis").querySelector("pre"); pre.hidden = !pre.hidden; return; }
  if (t.dataset.act === "flipall") { $$(".card", t.closest("div").parentElement).forEach(c => c.classList.toggle("flip")); return; }
  if (t.classList.contains("copy")) { navigator.clipboard?.writeText(t.nextElementSibling.textContent); t.textContent = "COPIED"; setTimeout(() => t.textContent = "COPY", 1200); return; }
  const card = t.closest(".card"); if (card) { card.classList.toggle("flip"); return; }
  if (t.matches(".slides img, .printout-body img")) { e.stopPropagation(); const lb = $("#lightbox"); $("img", lb).src = t.dataset.full || t.src; lb.hidden = false; return; }
  const src = t.closest(".src"); if (src) { openNote(src.dataset.path, src.dataset.heading); return; }
  if (t.classList.contains("wl") && t.dataset.path.includes("/")) { openNote(t.dataset.path.endsWith(".md") ? t.dataset.path : t.dataset.path + ".md"); return; }
});
$("#lightbox").onclick = () => $("#lightbox").hidden = true;
$("#po-close").onclick = () => $("#printout").hidden = true;
$("#printout").onclick = e => { if (e.target.id === "printout") $("#printout").hidden = true; };
$("#po-ask").onclick = () => {
  input.value = `Walk me through the key ideas in "${$("#po-body").dataset.title}"`; autosize();
  $("#printout").hidden = true; input.focus();
};
$("#deck").classList.toggle("expanded", store.get("deck", false));
$("#deck-toggle").onclick = () => { const on = $("#deck").classList.toggle("expanded"); store.set("deck", on); };
$("#toggle-side").onclick =() => { $("#side").classList.toggle("open"); $("#sources").classList.remove("open"); };
$("#toggle-src").onclick = () => { $("#sources").classList.toggle("open"); $("#side").classList.remove("open"); };
$("#reindex").onclick = async () => {
  const btn = $("#reindex");
  if (btn.disabled) return;
  btn.disabled = true; btn.textContent = "READING...";
  toast("Re-reading the vault: looking for new or changed notes...", 0);
  try {
    const before = (await (await fetch("api/status")).json()).last || 0;
    await fetch("api/reindex", { method: "POST" });
    let st = null;
    for (let i = 0; i < 120; i++) {
      await new Promise(r => setTimeout(r, 1000));
      st = await (await fetch("api/status")).json();
      btn.textContent = st.running ? `READING ${st.done}/${st.total}` : "READING...";
      if (!st.running && (st.last || 0) > before) break;
    }
    S.status = st; refreshStatus();
    toast(st.error ? "Re-read failed: " + st.error : st.changed ? `Done: ${st.changed} new or changed note${st.changed === 1 ? "" : "s"} indexed. ${st.files} notes searchable.` : `Done: nothing new. ${st.files} notes searchable and up to date.`, 6000);
  } catch (e) { toast("Re-read failed: " + e.message, 6000); }
  btn.disabled = false; btn.textContent = "RE-READ VAULT";
};
$("#unload").onclick = async () => {
  const b = $("#unload"); b.disabled = true; b.textContent = "UNLOADING...";
  try {
    const r = await fetch("api/unload", { method: "POST" });
    if (!r.ok) toast(await apiErr(r), 5000);
    else { const d = await r.json(); toast((d.unloaded.length ? `Unloaded ${d.unloaded.join(", ")}. ` : "Nothing of the study models was loaded. ") + (d.kept_busy ? "The shared model stays loaded because the vault agent is working right now. " : "") + `${(d.mem_free / 1e9).toFixed(1)} GB RAM free.`, 7000); }
  } catch (e) { toast("Could not unload: " + e.message, 5000); }
  b.disabled = false; b.textContent = "UNLOAD MODELS"; refreshStatus();
};

document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); newChat(); }
  if (e.key === "Escape") { $("#printout").hidden = true; $("#lightbox").hidden = true; $("#side").classList.remove("open"); $("#sources").classList.remove("open"); }
  if (e.key === "/" && document.activeElement === document.body) { e.preventDefault(); input.focus(); }
});

/* ================================================================ status lamps */

async function refreshStatus() {
  try {
    const st = await (await fetch("api/status")).json();
    if (st.vault && S.lastDone !== undefined && S.lastDone !== st.vault.done && S.cfg && !S.busy) {   // new notes finished: refresh subjects and starter questions
      S.lastDone = st.vault.done;
      try { const cfg = await (await fetch("api/config")).json(); S.cfg = cfg; renderControls(); if (!S.messages.length) renderLog(); } catch { }
    }
    if (st.vault && S.lastDone === undefined) S.lastDone = st.vault.done;
    S.status = st;
    const word = { idle: "IDLE", answering: "ANSWERING", reading: "READING NOTES" }[st.state] || "IDLE";
    $("#sys-word").textContent = word;
    $("#sys-dot").className = st.state === "idle" ? "ok" : "busy";
    $("#vault-dot").className = st.vault?.working ? "busy" : st.vault?.pending ? "wait" : "ok";
    const v = st.vault || {};
    $("#idx-line").textContent = st.error ? "INDEX ERROR: " + st.error.slice(0, 60)
      : st.running ? `INDEXING ${st.done}/${st.total}...`
      : v.working ? `PROCESSING ${v.done}/${v.total} DOCS`
      : v.pending ? `${v.pending} DOCS QUEUED` : `${st.files} NOTES · UP TO DATE`;
    if (S.cfg) {
      S.cfg.models = st.models; renderControls();
    }
  } catch {
    $("#sys-word").textContent = "OFFLINE"; $("#sys-dot").className = "bad";
  }
}

(async function boot() {
  document.documentElement.dataset.theme = S.theme;
  const cr = await fetch("api/config");
  if (cr.status === 401) { location.href = "login"; return; }
  try { S.me = await (await fetch("api/me")).json(); } catch { }
  if (S.me) {
    if (S.me.mode !== "shared") document.documentElement.classList.add("solo");   // one person: no logins, people or group chat
    const p = S.me.prefs || {};   // account preferences follow you across devices and win over this browser's settings
    for (const k of ["theme", "model", "mode", "depth"]) if (p[k]) { S[k] = p[k]; store.set(k, p[k]); }
    document.documentElement.style.setProperty("--fs", p.size || 1);
    document.dispatchEvent(new CustomEvent("s80-me", { detail: S.me }));
  }
  S.cfg = await cr.json();
  if (S.subject !== "ALL" && !S.cfg.subjects.some(s => s.id === S.subject)) S.subject = "ALL";
  if (!S.cfg.modes.some(m => m.id === S.mode)) S.mode = "teach";
  renderControls();
  await refreshStatus();
  renderLog(); loadChats();
  setInterval(refreshStatus, 8000);
  input.focus();
})();
