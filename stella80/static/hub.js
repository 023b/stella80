"use strict";
/* Board hub (leaderboard, study log, vault info) and the settings panel. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MODE_COL = { teach: "#ffb547", exam: "#4fc3f7", derive: "#c779d0", quiz: "#7bc96f", cards: "#f06292", code: "#26a69a", simple: "#d4e157", brief: "#90a4ae" };
  const dur = s => s < 60 ? "0 min" : s < 3600 ? Math.round(s / 60) + " min" : Math.floor(s / 3600) + " h " + String(Math.round((s % 3600) / 60)).padStart(2, "0") + " min";
  const ago = t => { const s = Math.max(0, Date.now() / 1000 - t); return s < 90 ? "just now" : s < 3600 ? Math.round(s / 60) + " min ago" : s < 86400 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " d ago"; };
  const dayName = d => { const t = new Date(d + "T12:00:00"); const n = Math.round((new Date(new Date().toDateString()) - new Date(t.toDateString())) / 864e5);
    return n === 0 ? "Today" : n === 1 ? "Yesterday" : t.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" }); };
  const label = id => {
    const sj = typeof subjOf === "function" ? subjOf(id) : null;
    if (sj && !/^[A-Z]{3,4}M?\d{3,4}$/.test(sj.id)) return sj.name + (sj.code ? " (" + sj.code + ")" : "");
    const m = /^([A-Z]{3,4}M?\d{3,4})/.exec(id || ""); return m ? ((typeof SHORT !== "undefined" && SHORT[m[1]]) || m[1]) : String(id || "").replace(/-/g, " ");
  };
  const json = async (url, opt) => {
    const r = await fetch(url, opt);
    if (!r.ok) { let t = await r.text(); try { t = JSON.parse(t).detail || t; } catch { } throw new Error(t); }
    return r.json();
  };

  let tab = "board", period = "today", logUser = "", names = [], timer = null, gen = 0;
  const out = g => g === gen ? $("#hub-body") : {};   // a slow response for a tab you have already left must not overwrite the current one

  /* ---------------- hub shell */
  function openHub(t) { $("#tab-admin").hidden = S.me?.role !== "admin"; $("#hub").hidden = false; show(t || tab); }
  function closeHub() { $("#hub").hidden = true; clearInterval(timer); }
  function show(t) {
    tab = t; gen++;
    $$("#hub-tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
    clearInterval(timer);
    const go = { board: renderBoard, log: renderLog, vault: renderVault, admin: renderAdmin }[t];
    go();
    if (t !== "admin") timer = setInterval(go, t === "log" ? 60000 : 15000);   // the admin form is not refreshed under your hands
  }
  $("#hub-open").onclick = () => openHub("board");
  $("#vault-line").onclick = () => openHub("vault");
  $("#hub-close").onclick = closeHub;
  $("#hub").onclick = e => { if (e.target.id === "hub") closeHub(); };
  $("#hub-tabs").onclick = e => { const b = e.target.closest("button"); if (b) show(b.dataset.tab); };

  /* ---------------- board */
  const mixBar = modes => {
    const tot = Object.values(modes).reduce((a, b) => a + b, 0);
    if (!tot) return "";
    return `<div class="mix" title="${esc(Object.entries(modes).map(([k, v]) => k + " " + v).join(", "))}">${Object.entries(modes).sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `<i style="width:${100 * v / tot}%;background:${MODE_COL[k] || "#888"}"></i>`).join("")}</div>
      <div class="mixkey">${Object.entries(modes).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<span><b style="background:${MODE_COL[k] || "#888"}"></b>${esc(k)} ${v}</span>`).join("")}</div>`;
  };
  async function renderBoard() {
    const g = gen;
    let d;
    try { d = await json("api/board?period=" + period); } catch { out(g).innerHTML = `<p class="muted">Could not load the board.</p>`; return; }
    names = d.rows.map(r => ({ name: r.name, display: r.display }));
    const any = d.rows.some(r => r.score > 0);
    out(g).innerHTML = `<div class="bar"><div class="seg" id="period-seg">${[["today", "TODAY"], ["week", "7 DAYS"], ["all", "ALL TIME"]].map(([v, l]) =>
      `<button data-v="${v}" class="${period === v ? "on" : ""}">${l}</button>`).join("")}</div><span class="muted">${esc(d.formula)}</span></div>
      ${any ? "" : `<p class="muted pad">Nothing recorded for this period yet. Time counts while this tab is open and you are active; questions count when you ask Stella.</p>`}
      ${d.rows.map((r, i) => `<article class="bcard ${r.name === d.you ? "you" : ""}" style="--c:${esc(r.color)}">
        <div class="rank">${any ? i + 1 : "-"}</div>
        <div class="bmain">
          <div class="bname"><span class="od ${r.online ? "on" : ""}"></span>${esc(r.display)}${r.name === d.you ? " (you)" : ""}<small>${esc(r.group)}</small>${r.streak > 1 ? `<span class="streak">${r.streak} day streak</span>` : ""}</div>
          <div class="bstats"><b>${dur(r.seconds)}</b> active <span>|</span> <b>${r.questions}</b> question${r.questions === 1 ? "" : "s"} <span>|</span> <b>${r.recall}</b> recall</div>
          ${mixBar(r.modes)}
          ${r.subjects.length || r.topics.length ? `<div class="tags">${r.subjects.map(([s, n]) => `<span class="tg sj">${esc(label(s))} ${n}</span>`).join("")}${r.topics.map(([t]) => `<span class="tg">${esc(t)}</span>`).join("")}</div>` : ""}
          ${r.summary ? `<p class="bsum">${esc(r.summary)} <small>${esc(dayName(r.summary_day))}</small></p>` : (r.shared ? "" : `<p class="bsum dim">Keeps their summary private.</p>`)}
        </div>
        <div class="bscore">${r.score}<small>pts</small></div></article>`).join("")}`;
  }
  $("#hub-body").addEventListener("click", e => {
    const b = e.target.closest("#period-seg button");
    if (b) { period = b.dataset.v; renderBoard(); }
    if (e.target.closest("[data-upload]")) { document.dispatchEvent(new CustomEvent("s80-upload", { detail: { vault: vaultSel } })); return; }
    const lb = e.target.closest("[data-reindex]");
    if (lb) { $("#reindex").click(); }
  });

  /* ---------------- study log */
  async function renderLog() {
    const g = gen;
    if (!names.length) { try { names = (await json("api/board?period=all")).rows.map(r => ({ name: r.name, display: r.display })); } catch { } }
    let d;
    try { d = await json("api/log" + (logUser ? "?user=" + encodeURIComponent(logUser) : "")); } catch { out(g).innerHTML = `<p class="muted">Could not load the log.</p>`; return; }
    const open = new Set($$("#hub-body details[open]").map(x => x.dataset.day));
    out(g).innerHTML = `<div class="bar"><label class="muted">Whose log
      <select id="log-user">${names.map(n => `<option value="${esc(n.name)}" ${n.name === d.user ? "selected" : ""}>${esc(n.display)}</option>`).join("")}</select></label>
      <span class="muted">${d.own ? "Your questions are only visible to you." : "Shared summary view."}</span></div>
      ${d.private ? `<p class="muted pad">${esc(d.display)} keeps their log private.</p>` : !d.days.length ? `<p class="muted pad">No study recorded in the last 30 days.</p>` : ""}
      ${d.days.map(x => `<article class="lcard">
        <div class="lhead"><b>${esc(dayName(x.day))}</b><span>${dur(x.seconds)} active | ${x.questions} question${x.questions === 1 ? "" : "s"} | ${x.recall} recall | ${x.score} pts</span></div>
        ${x.summary ? `<p class="bsum">${esc(x.summary)}</p>` : ""}
        ${mixBar(x.modes)}
        ${x.subjects.length || x.topics.length ? `<div class="tags">${x.subjects.map(([s, n]) => `<span class="tg sj">${esc(label(s))} ${n}</span>`).join("")}${x.topics.map(([t]) => `<span class="tg">${esc(t)}</span>`).join("")}</div>` : ""}
        ${x.items && x.items.length ? `<details data-day="${x.day}" ${open.has(x.day) ? "open" : ""}><summary>${x.items.length} question${x.items.length === 1 ? "" : "s"}</summary>
          <ul class="qs">${x.items.map(q => `<li><time>${new Date(q.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time><i style="background:${MODE_COL[q.mode] || "#888"}" title="${esc(q.mode)}"></i>${esc(q.q)}</li>`).join("")}</ul></details>` : ""}
      </article>`).join("")}`;
    const sel = $("#log-user");
    if (sel) sel.onchange = () => { logUser = sel.value; renderLog(); };
  }

  /* ---------------- vault */
  let vaultSel = "", qsel = new Set(), showAllQ = false, browse = false, browseQ = "", browseRows = null;
  const sz = m => m == null ? "" : m < 1 ? Math.max(1, Math.round(m * 1000)) + " KB" : m.toFixed(1) + " MB";
  const stChip = s => `<span class="chip2 ${s === "done" ? "processed" : s === "pending" ? "queued" : ""}">${esc(s)}</span>`;
  function browseHtml(edit) {
    if (!browseRows) return `<p class="muted">Loading...</p>`;
    if (!browseRows.length) return `<p class="muted">No files match.</p>`;
    return `<ul class="vl queue">${browseRows.map(r => `<li class="qrow" data-id="${r.id}"><span class="nm" title="${esc(r.file)}">${esc(r.title)}</span><span class="mt">${esc(label(r.course))}</span>
      <span class="chip2">${esc(r.type || "file")}</span>${stChip(r.status)}
      ${edit ? `<span class="qbtn">${r.status === "excluded" ? `<button data-qact="include" data-one type="button">PROCESS AGAIN</button>` :
        r.status === "pending" ? `<button data-qact="top" data-one type="button">TOP</button><button data-qact="exclude" data-one type="button">REMOVE</button>` :
        `<button data-qact="reprocess" data-one type="button" title="Run the model over this file again">REPROCESS</button><button data-qact="exclude" data-one type="button" title="Take it out of the vault">REMOVE</button>`}</span>` : ""}</li>`).join("")}</ul>`;
  }
  async function loadBrowse() {
    if (!browse) return;
    try { browseRows = (await json("api/queue/docs?vault=" + encodeURIComponent(vaultSel) + (browseQ ? "&q=" + encodeURIComponent(browseQ) : ""))).docs; }
    catch { browseRows = []; }
    const box = $("#browse-list"); if (box) box.innerHTML = browseHtml(true);
  }
  async function renderVault() {
    const g = gen;
    if (document.activeElement && document.activeElement.id === "browse-q") return;   // do not rebuild the page while you type a search
    let v, q;
    try {
      [v, q] = await Promise.all([json("api/vault" + (vaultSel ? "?vault=" + encodeURIComponent(vaultSel) : "")), json("api/queue" + (vaultSel ? "?vault=" + encodeURIComponent(vaultSel) : ""))]);
    } catch { out(g).innerHTML = `<p class="muted">Could not load vault info.</p>`; return; }
    if (!vaultSel) vaultSel = q.vault;
    const pr = q.progress, edit = q.can_edit;
    const state = pr.working && pr.phase === "vision" ? `Looking at slide images: <b>${esc(pr.name)}</b>, page ${pr.page}` :
      pr.working ? `Reading <b>${esc(pr.name)}</b>${pr.of ? ` (document ${pr.n} of ${pr.of} this run)` : ""}${pr.chunks ? `, chunk ${pr.chunk} of ${pr.chunks}` : ""}` :
      v.pending_n ? `${v.pending_n} document${v.pending_n === 1 ? "" : "s"} queued. Nothing is being read at this moment; another vault may be using the model, and the queue starts by itself when it is free` :
      v.figures_todo ? `${v.figures_todo} slide images still to look at` : "Everything received so far is processed";
    const chip = s => `<span class="chip2 ${s}">${esc(s)}</span>`;
    const list = (rows, f) => rows.length ? `<ul class="vl">${rows.map(f).join("")}</ul>` : `<p class="muted">None.</p>`;
    const keep = [...qsel].filter(id => q.next.some(r => r.id === id));
    qsel = new Set(keep);
    const rows = showAllQ ? q.next : q.next.slice(0, 40);
    const tools = edit ? `<div class="qtools"><label class="chkall"><input type="checkbox" id="q-all"> all</label>
      <button class="key key-cream small" data-qact="top" type="button">PRIORITISE</button><button class="key key-cream small" data-qact="bottom" type="button">DO LAST</button>
      <button class="key key-cream small" data-qact="normal" type="button">NORMAL</button><button class="key key-cream small" data-qact="exclude" type="button">REMOVE</button>
      <span class="muted">${qsel.size ? qsel.size + " selected" : "tick files, or use the buttons on a row"}</span></div>` : "";
    out(g).innerHTML = `${q.vaults.length > 1 ? `<div class="bar"><div class="seg" id="vault-seg">${q.vaults.map(x => `<button data-v="${esc(x.id)}" class="${x.id === q.vault ? "on" : ""}">${esc(x.title.toUpperCase())}</button>`).join("")}</div></div>` : ""}
      <div class="bar"><b class="vt">${esc(v.title)}</b><span class="muted">${v.notes} searchable notes | ${v.done} of ${v.total} documents turned into notes${v.skipped ? ` | ${v.skipped} skipped (binary, over 25 MB or removed)` : ""}</span>
        <button class="key key-orange small" data-upload type="button">UPLOAD FILES</button> <button class="key key-cream small" data-reindex type="button">RE-READ VAULT</button></div>
      <div class="vstate ${pr.working ? "busy" : ""}"><span class="od ${pr.working ? "on" : ""}"></span><span>${state}.</span></div>
      ${pr.working && pr.chunks ? `<div class="meter big"><i style="width:${100 * pr.chunk / pr.chunks}%"></i></div>` : ""}
      <h4>Up next (${q.next.length})</h4>${q.next.length ? tools : ""}
      ${q.next.length ? `<ul class="vl queue">${rows.map(r => `<li class="qrow ${r.current ? "cur" : ""}" data-id="${r.id}">
        ${edit ? `<input type="checkbox" class="qc" ${qsel.has(r.id) ? "checked" : ""}>` : ""}<span class="pos">${r.current ? "NOW" : "#" + r.position}</span>
        <span class="nm" title="${esc(r.file)}">${esc(r.title)}</span><span class="mt">${esc(label(r.course))}</span>${chip(r.type || "file")}<span class="mt">${sz(r.size_mb)}</span>
        ${r.priority > 0 ? `<span class="chip2 queued">priority</span>` : r.priority < 0 ? `<span class="chip2">later</span>` : ""}
        ${edit ? `<span class="qbtn"><button data-qact="top" data-one type="button" title="Process first">TOP</button><button data-qact="bottom" data-one type="button" title="Process last">LAST</button><button data-qact="exclude" data-one type="button" title="Do not process">REMOVE</button></span>` : ""}</li>`).join("")}</ul>
        ${q.next.length > 40 ? `<button class="key key-cream small" id="q-more" type="button">${showAllQ ? "SHOW FIRST 40" : "SHOW ALL " + q.next.length}</button>` : ""}` : `<p class="muted">Nothing waiting.</p>`}
      ${q.excluded.length ? `<h4>Removed from processing (${q.excluded.length})</h4><ul class="vl queue">${q.excluded.map(r => `<li class="qrow" data-id="${r.id}"><span class="nm">${esc(r.title)}</span><span class="mt">${esc(label(r.course))}</span>${chip(r.type || "file")}
        ${edit ? `<span class="qbtn"><button data-qact="include" data-one type="button">PROCESS AGAIN</button></span>` : ""}</li>`).join("")}</ul>` : ""}
      <h4>All files in this vault</h4>
      <div class="bar"><button class="key key-cream small" id="browse-toggle" type="button">${browse ? "HIDE FILE LIST" : "BROWSE ALL FILES"}</button>
        ${browse ? `<input id="browse-q" placeholder="search by name..." value="${esc(browseQ)}" autocomplete="off">` : ""}</div>
      ${browse ? `<div id="browse-list">${browseHtml(q.can_edit)}</div>` : ""}
      <h4>Courses</h4>${v.courses.length ? v.courses.map(c => `<div class="crow"><span>${esc(label(c.course))}</span><div class="meter"><i style="width:${c.total ? 100 * c.done / c.total : 0}%"></i></div><span class="num">${c.done}/${c.total}</span></div>`).join("") : `<p class="muted">No courses yet.</p>`}
      <h4>Recently received</h4>${list(v.received, r => `<li><span class="nm">${esc(r.name)}</span><span class="mt">${esc(label(r.course))}</span>${chip(r.state)}<time>${ago(r.t)}</time></li>`)}
      <h4>Recently processed notes</h4>${list(v.processed, r => `<li><span class="nm">${esc(r.title)}</span><span class="mt">${esc(label(r.course))}</span><time>${ago(r.t)}</time></li>`)}`;
    if (browse && g === gen) loadBrowse();
  }
  let browseT;
  $("#hub-body").addEventListener("input", e => {
    if (e.target.id !== "browse-q") return;
    browseQ = e.target.value; clearTimeout(browseT); browseT = setTimeout(loadBrowse, 250);
  });
  $("#hub-body").addEventListener("change", e => {
    if (e.target.id === "q-all") { $$("#hub-body .qc").forEach(c => { c.checked = e.target.checked; const id = +c.closest(".qrow").dataset.id; e.target.checked ? qsel.add(id) : qsel.delete(id); }); }
    else if (e.target.classList.contains("qc")) { const id = +e.target.closest(".qrow").dataset.id; e.target.checked ? qsel.add(id) : qsel.delete(id); }
  });
  $("#hub-body").addEventListener("click", async e => {
    const seg = e.target.closest("#vault-seg button");
    if (seg) { vaultSel = seg.dataset.v; qsel.clear(); renderVault(); return; }
    if (e.target.id === "q-more") { showAllQ = !showAllQ; renderVault(); return; }
    if (e.target.id === "browse-toggle") { browse = !browse; browseRows = null; renderVault(); return; }
    const b = e.target.closest("[data-qact]");
    if (!b) return;
    const ids = b.dataset.one !== undefined ? [+b.closest(".qrow").dataset.id] : [...qsel];
    if (!ids.length) { toast("Tick one or more files first.", 2500); return; }
    if (b.dataset.qact === "exclude" && !confirm(`Remove ${ids.length} file${ids.length === 1 ? "" : "s"} from processing? A processed file also leaves your graph at the next render. The files stay on Stella and you can process them again later.`)) return;
    if (b.dataset.qact === "reprocess" && !confirm("Run the model over this file again? Its notes are rebuilt when it finishes.")) return;
    try {
      await json("api/queue/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vault: vaultSel, action: b.dataset.qact, ids }) });
      toast({ top: "Moved to the front.", bottom: "Moved to the back.", normal: "Back to normal order.", exclude: "Removed from processing.", include: "Will be processed again.", reprocess: "Queued to be processed again, first in line." }[b.dataset.qact], 2500);
      qsel.clear(); browseRows = null; renderVault();
    } catch (err) { toast("Could not change the queue: " + err.message, 4000); }
  });

  /* ---------------- admin console */
  const ENG = [["quick", "QUICK"], ["scholar", "SCHOLAR"], ["deep", "DEEP"]];
  async function renderAdmin() {
    const g = gen;
    let d;
    try { d = await json("api/admin/users"); } catch { out(g).innerHTML = `<p class="muted">Admin only.</p>`; return; }
    const sw = (u, k, text) => `<label><input type="checkbox" data-k="${k}" ${u.limits[k] ? "checked" : ""}> ${text}</label>`;
    const add = d.mode !== "shared" ? `<p class="muted">Solo mode has no accounts. Set <code>mode: shared</code> in stella80.yaml and restart to add people.</p>` :
      `<form class="ucard uadd" id="uadd"><div class="uhead"><span class="nm">Add a person</span><small>They get a passphrase shown once here. Send it to them privately.</small></div>
        <div class="ugrid">
          <label>Login name <input name="name" required maxlength="20" pattern="[a-zA-Z0-9_-]+" placeholder="sam" autocomplete="off"></label>
          <label>Shown as <input name="display" maxlength="20" placeholder="Sam" autocomplete="off"></label>
          ${d.vaults.length > 1 ? `<label>Vault <select name="vault">${d.vaults.map(v => `<option value="${esc(v.id)}">${esc(v.title)}</option>`).join("")}</select></label>` : ""}
          <label><input type="checkbox" name="admin"> Admin (can add people and change limits)</label>
        </div>
        <div class="urow"><button class="key key-orange small" type="submit">ADD</button><span data-addout></span></div></form>`;
    out(g).innerHTML = add + `<p class="muted">Changes apply immediately, on the person's next question. Turning something off hides or disables it in their interface and is also enforced on the server.</p>` +
      d.users.map(u => `<article class="ucard ${u.limits.locked ? "locked-acct" : ""}" data-user="${esc(u.name)}" style="--c:${esc(u.color)}">
        <div class="uhead"><span class="nm">${esc(u.display)}</span><small>${esc(u.vault)} | ${u.role}${u.has_pass ? "" : " | no passphrase"}</small>
          <small>${u.online ? "online now" : "last seen " + (u.last_seen ? ago(u.last_seen) : "never")} | today ${u.q_today} question${u.q_today === 1 ? "" : "s"}, ${dur(u.secs_today)} | all time ${u.q_all}</small><span class="saved" data-saved>saved</span></div>
        ${u.role === "admin" ? `<p class="muted">Admin: no limits apply.</p>` : `
        <div class="ugrid">
          <label><input type="checkbox" data-k="locked" ${u.limits.locked ? "" : "checked"} data-invert> Account active</label>
          ${sw(u, "upload", "Uploads")}${sw(u, "think", "THINK switch")}${sw(u, "visuals", "Plots and diagrams")}${sw(u, "images", "Image attachments")}${sw(u, "custom_persona", "Custom tutor instructions")}
          <div class="eng">Engines: ${ENG.map(([k, l]) => `<label><input type="checkbox" data-model="${k}" ${u.limits.models.includes(k) ? "checked" : ""}> ${l}</label>`).join(" ")}</div>
          <label>Deepest answers <select data-k="max_depth">${["brief", "normal", "deep"].map(v => `<option value="${v}" ${u.limits.max_depth === v ? "selected" : ""}>${v.toUpperCase()}</option>`).join("")}</select></label>
          <label>Questions per day <input class="num" type="number" min="0" max="10000" data-k="daily_q" value="${u.limits.daily_q}"> <span class="muted">0 = no limit</span></label>
        </div>`}
        <div class="urow"><button class="key key-cream small" data-newpw type="button">NEW PASSPHRASE</button>${u.name === S.me.name ? "" : `<button class="key small" data-remove type="button">REMOVE</button>`}<span data-pwout></span></div>
      </article>`).join("");
  }
  async function adminPut(card, patch) {
    try {
      await json("api/admin/users/" + card.dataset.user, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
      const s = $("[data-saved]", card); s.classList.add("on"); setTimeout(() => s.classList.remove("on"), 1500);
      if (patch.locked !== undefined) card.classList.toggle("locked-acct", patch.locked);
    } catch (e) { toast("Could not save: " + e.message, 4000); renderAdmin(); }
  }
  $("#hub-body").addEventListener("change", e => {
    const card = e.target.closest(".ucard"); if (!card) return;
    const t = e.target;
    if (t.dataset.model) { const models = $$("[data-model]", card).filter(x => x.checked).map(x => x.dataset.model); if (!models.length) { t.checked = true; return; } adminPut(card, { models }); }
    else if (t.dataset.k === "daily_q") adminPut(card, { daily_q: Math.max(0, parseInt(t.value || "0", 10) || 0) });
    else if (t.dataset.k === "max_depth") adminPut(card, { max_depth: t.value });
    else if (t.dataset.k) adminPut(card, { [t.dataset.k]: t.dataset.invert !== undefined ? !t.checked : t.checked });
  });
  $("#hub-body").addEventListener("submit", async e => {
    if (e.target.id !== "uadd") return;
    e.preventDefault();
    const f = e.target, fd = new FormData(f);
    try {
      const r = await json("api/admin/users", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: fd.get("name"), display: fd.get("display"), vault: fd.get("vault") || "", admin: fd.get("admin") === "on" }) });
      await renderAdmin();
      $("[data-addout]").innerHTML = `Added <b>${esc(r.name)}</b>. Passphrase: <code class="newpw">${esc(r.passphrase)}</code> <span class="muted">shown once, send it to them privately</span>`;
    } catch (err) { toast("Could not add: " + err.message, 4000); }
  });
  $("#hub-body").addEventListener("click", async e => {
    const rm = e.target.closest("[data-remove]");
    if (rm) {
      const card = rm.closest(".ucard"), nm = card.querySelector(".nm").textContent;
      if (!confirm(`Remove ${nm}? Their account, chats and study history are deleted. This cannot be undone.`)) return;
      try { await json("api/admin/users/" + card.dataset.user, { method: "DELETE" }); renderAdmin(); }
      catch (err) { toast("Could not remove: " + err.message, 4000); }
      return;
    }
    const b = e.target.closest("[data-newpw]"); if (!b) return;
    const card = b.closest(".ucard");
    if (!confirm(`Give ${card.querySelector(".nm").textContent} a new passphrase? The old one stops working.`)) return;
    try {
      const r = await json(`api/admin/users/${card.dataset.user}/passphrase`, { method: "POST" });
      $("[data-pwout]", card).innerHTML = `<code class="newpw">${esc(r.passphrase)}</code> <span class="muted">shown once, send it to them privately</span>`;
    } catch (err) { toast("Could not set it: " + err.message, 4000); }
  });

  /* ---------------- settings */
  const SIZES = [[0.9, "S"], [1, "M"], [1.15, "L"], [1.3, "XL"]];
  let saveT;
  async function save(patch) {
    Object.assign(S.me.prefs, Object.fromEntries(Object.entries(patch).filter(([k]) => !["display", "color"].includes(k))));
    if (patch.color) { S.me.color = patch.color; $("#me-dot").style.background = patch.color; }
    if (patch.display && patch.display.trim()) { S.me.display = patch.display.trim(); $("#me-name").textContent = S.me.display.toUpperCase(); }
    clearTimeout(saveT);
    saveT = setTimeout(async () => {
      try {
        const r = await json("api/prefs", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
        S.me.display = r.display; S.me.color = r.color;
        $("#me-name").textContent = r.display.toUpperCase(); $("#me-dot").style.background = r.color;
        if (window.s80Beat) window.s80Beat();   // refresh the coloured initials in the header now
        toast("Saved.", 1500);
      } catch (e) { toast("Could not save that setting.", 3000); }
    }, 250);
  }
  function openSettings() {
    const p = S.me.prefs || {}, models = S.cfg.models, modes = S.cfg.modes;
    $("#settings-body").innerHTML = `
      <section><h4>Profile</h4>
        <label class="fld">Display name <input id="st-name" maxlength="20" value="${esc(S.me.display)}"></label>
        <div class="fld">Colour <div class="sw" id="st-colors">${S.me.palette.map(c => `<button type="button" data-c="${c}" class="${c.toLowerCase() === S.me.color.toLowerCase() ? "on" : ""}" style="background:${c}" aria-label="${c}"></button>`).join("")}</div></div>
      </section>
      <section><h4>Look</h4>
        <div class="fld">Screen <div class="seg" id="st-theme">${[["oled", "OLED"], ["amber", "AMBER"], ["green", "P1"], ["paper", "PAPER"]].map(([v, l]) => `<button data-v="${v}" class="${S.theme === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
        <div class="fld">Text size <div class="seg" id="st-size">${SIZES.map(([v, l]) => `<button data-v="${v}" class="${(p.size || 1) === v ? "on" : ""}">${l}</button>`).join("")}</div></div>
      </section>
      <section><h4>Defaults for new questions</h4>
        <label class="fld">Engine <select id="st-model">${models.map(m => `<option value="${m.id}" ${S.model === m.id ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</select></label>
        <label class="fld">Mode <select id="st-mode">${modes.map(m => `<option value="${m.id}" ${S.mode === m.id ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</select></label>
        <label class="fld">Depth <select id="st-depth">${["brief", "normal", "deep"].map(v => `<option value="${v}" ${S.depth === v ? "selected" : ""}>${v.toUpperCase()}</option>`).join("")}</select></label>
      </section>
      <section><h4>How the tutor talks to you</h4>
        <label class="fld">Style <select id="st-persona">${Object.entries(S.me.personas).map(([k, v]) => `<option value="${k}" ${(p.persona || "default") === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
        ${S.me.limits.custom_persona ? `<label class="fld col">Your own instructions (optional)
          <textarea id="st-custom" maxlength="600" rows="3" placeholder="For example: always give a worked example, use British spelling, keep answers under 150 words">${esc(p.custom || "")}</textarea></label>`
          : `<p class="muted">Custom instructions are turned off for your account.</p>`}
      </section>
      <section class="acct-only"><h4>Privacy</h4>
        <label class="chk"><input type="checkbox" id="st-share" ${p.share === false ? "" : "checked"}> Show my topics and daily summary on the board. Time, questions and points are always shown.</label>
        <p class="muted">The exact questions you ask are never shown to anyone else.</p>
      </section>
      <section class="acct-only"><h4>Passphrase</h4>
        <label class="fld">Current <input type="password" id="pw-cur" autocomplete="current-password"></label>
        <label class="fld">New <input type="password" id="pw-new" autocomplete="new-password"></label>
        <label class="fld">Again <input type="password" id="pw-new2" autocomplete="new-password"></label>
        <div class="fld"><button class="key key-orange small" id="pw-go" type="button">CHANGE</button><span id="pw-msg" class="muted"></span></div>
      </section>
      <section><h4>Chats</h4>
        <div class="fld"><span class="muted">Delete every session in your sidebar. This cannot be undone.</span><button class="key key-cream small" id="del-all" type="button">DELETE ALL CHATS</button></div>
        <p class="muted">To delete one, hover over it in the sidebar and press the cross.</p>
      </section>
      <section class="foot acct-only"><a class="key key-cream small" href="logout">LOG OUT</a></section>`;
    $("#settings").hidden = false;
    $("#st-name").onchange = e => save({ display: e.target.value });
    $("#st-colors").onclick = e => { const b = e.target.closest("button"); if (!b) return; $$("#st-colors button").forEach(x => x.classList.toggle("on", x === b)); save({ color: b.dataset.c }); };
    $("#st-theme").onclick = e => { const b = e.target.closest("button"); if (!b) return; $$("#st-theme button").forEach(x => x.classList.toggle("on", x === b)); setOpt("theme", b.dataset.v); save({ theme: b.dataset.v }); };
    $("#st-size").onclick = e => { const b = e.target.closest("button"); if (!b) return; $$("#st-size button").forEach(x => x.classList.toggle("on", x === b));
      document.documentElement.style.setProperty("--fs", b.dataset.v); save({ size: +b.dataset.v }); };
    for (const [id, k] of [["st-model", "model"], ["st-mode", "mode"], ["st-depth", "depth"]]) $("#" + id).onchange = e => { setOpt(k, e.target.value); save({ [k]: e.target.value }); };
    $("#st-share").onchange = e => save({ share: e.target.checked });
    $("#st-persona").onchange = e => save({ persona: e.target.value });
    const cu = $("#st-custom"); if (cu) cu.onchange = e => save({ custom: e.target.value });
    $("#del-all").onclick = async () => {
      if (!confirm("Delete ALL your chat sessions? This cannot be undone.")) return;
      const r = await json("api/chats", { method: "DELETE" });
      toast(`Deleted ${r.deleted} session${r.deleted === 1 ? "" : "s"}.`, 3000);
      newChat();
    };
    $("#pw-go").onclick = async () => {
      const msg = $("#pw-msg"), cur = $("#pw-cur").value, nw = $("#pw-new").value;
      if (nw !== $("#pw-new2").value) { msg.textContent = " The two new passphrases differ."; return; }
      try { await json("api/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current: cur, new: nw }) });
        msg.textContent = " Changed."; ["pw-cur", "pw-new", "pw-new2"].forEach(i => $("#" + i).value = ""); }
      catch (e) { try { msg.textContent = " " + JSON.parse(e.message).detail; } catch { msg.textContent = " Could not change it."; } }
    };
  }
  const closeSettings = () => { $("#settings").hidden = true; };
  $("#me-chip").onclick = openSettings;
  $("#settings-close").onclick = closeSettings;
  $("#settings").onclick = e => { if (e.target.id === "settings") closeSettings(); };
  document.addEventListener("keydown", e => { if (e.key === "Escape") { closeHub(); closeSettings(); } });
})();
