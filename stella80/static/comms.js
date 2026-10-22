"use strict";
/* Header widgets: presence dots, group chat drawer, status popover. Also the presence heartbeat. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const get = (k, d) => { try { const v = localStorage.getItem("s80." + k); return v === null ? d : JSON.parse(v); } catch { return d; } };
  const put = (k, v) => { try { localStorage.setItem("s80." + k, JSON.stringify(v)); } catch { } };

  let lastAct = Date.now();
  for (const ev of ["pointerdown", "keydown", "wheel", "touchstart"]) addEventListener(ev, () => { lastAct = Date.now(); }, { passive: true });
  let me = null, people = [], chatOpen = false, lastId = 0, seenId = get("room_seen", 0), msgs = [], sysT = null;

  const ago = t => {
    if (!t) return "never";
    const s = Math.max(0, Date.now() / 1000 - t);
    return s < 90 ? "just now" : s < 3600 ? Math.round(s / 60) + " min ago" : s < 86400 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " d ago";
  };
  const gb = b => (b / 1073741824).toFixed(1);

  document.addEventListener("s80-me", e => {
    me = e.detail;
    $("#me-chip").hidden = false;
    $("#me-name").textContent = me.display.toUpperCase();
    $("#me-dot").style.background = me.color;
    beat(); setInterval(beat, 20000);
    pollRoom(); setInterval(pollRoom, 3000);
    document.addEventListener("visibilitychange", beat);
  });

  /* ---------------- people */
  function renderPeople() {
    $("#people").innerHTML = people.map(p => `<span class="pdot ${p.online ? "on" : ""} ${p.doing === "asking Stella" ? "busy" : ""}" style="--c:${esc(p.color)}"
      title="${esc(p.display)}: ${p.online ? esc(p.doing) : "last seen " + ago(p.last_seen)}">${esc(p.display[0])}</span>`).join("");
  }
  window.s80Beat = () => beat();
  async function beat() {
    try {
      const active = !document.hidden && Date.now() - lastAct < 120000;   // visible tab and touched in the last two minutes
      const r = await fetch("api/presence", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ away: document.hidden, active }) });
      if (r.ok) { people = (await r.json()).online; renderPeople(); }
    } catch { }
  }

  /* ---------------- group chat */
  function roomHtml() {
    return msgs.map(m => {
      const mine = m.user === me.name, t = new Date(m.created * 1000);
      return `<div class="rm ${mine ? "mine" : ""} ${m.user === "stella" ? "sys" : ""}" style="--c:${esc(m.color || "#a8a290")}">
        <div class="who">${esc(m.display || m.user)}<small>${t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small>${mine || me.role === "admin" ? `<button class="x" data-del="${m.id}" title="Delete">&#x2715;</button>` : ""}</div>
        <div class="txt">${esc(m.text)}</div></div>`;
    }).join("") || `<p class="muted">No messages yet. Say hi.</p>`;
  }
  async function pollRoom() {
    if (!me) return;
    try {
      const r = await fetch("api/room" + (lastId ? "?since=" + lastId : ""));
      if (!r.ok) return;
      const rows = await r.json();
      if (rows.length) {
        const log = $("#room-log"), stick = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
        msgs = lastId ? msgs.concat(rows) : rows;
        lastId = msgs[msgs.length - 1].id;
        if (chatOpen) { log.innerHTML = roomHtml(); if (stick) log.scrollTop = 1e9; seenId = lastId; put("room_seen", seenId); }
      }
      badge();
    } catch { }
  }
  function badge() {
    const n = msgs.filter(m => m.id > seenId && m.user !== me?.name).length;
    const b = $("#comms-badge");
    b.hidden = !n || chatOpen;
    b.textContent = n > 9 ? "9+" : n;
  }
  $("#room-form").onsubmit = async e => {
    e.preventDefault();
    const i = $("#room-input"), text = i.value.trim();
    if (!text) return;
    i.value = "";
    await fetch("api/room", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    pollRoom();
  };
  $("#room-log").addEventListener("click", async e => {
    const id = e.target.dataset.del;
    if (!id) return;
    await fetch("api/room/" + id, { method: "DELETE" });
    msgs = msgs.filter(m => m.id != id); $("#room-log").innerHTML = roomHtml();
  });
  function openChat() {
    closeSys();
    chatOpen = true; $("#comms").hidden = false;
    $("#room-log").innerHTML = roomHtml(); $("#room-log").scrollTop = 1e9;
    seenId = lastId; put("room_seen", seenId); badge();
    $("#room-input").focus();
  }
  function closeChat() { chatOpen = false; $("#comms").hidden = true; }
  $("#chat-open").onclick = () => chatOpen ? closeChat() : openChat();
  $("#comms-close").onclick = closeChat;
  $("#comms").onclick = e => { if (e.target.id === "comms") closeChat(); };

  /* ---------------- status popover, in plain words */
  const level = (v, mid, hi) => v >= hi ? "hi" : v >= mid ? "mid" : "";
  const row = (k, pct, cls, v) => `<div class="sys-row"><span class="k">${k}</span><div class="meter"><i class="${cls}" style="width:${Math.min(100, Math.max(2, pct))}%"></i></div><span class="v">${v}</span></div>`;
  async function renderSystem() {
    let s;
    try { s = await (await fetch("api/system")).json(); } catch { $("#sys-body").innerHTML = `<p class="muted">Could not reach Stella.</p>`; return; }
    const used = s.mem_total - s.mem_avail, memPct = 100 * used / s.mem_total;
    const cpuWord = s.cpu < 25 ? "relaxed" : s.cpu < 60 ? "working" : s.cpu < 85 ? "busy" : "flat out";
    const memWord = s.mem_avail > 8e9 ? "plenty free for the AI models" : s.mem_avail > 3e9 ? "getting full, a big model may not fit" : "very full, answers fall back to the small model";
    const tWord = s.temp == null ? "" : s.temp < 55 ? "cool" : s.temp < 75 ? "warm" : "hot";
    const cr = s.crunch.map(c => {
      const state = c.working ? `reading <b>${esc(c.current || "files")}</b>` : c.pending ? "waiting its turn" : c.figures_todo ? `${c.figures_todo} slide images to go` : c.total ? "up to date" : "no files yet";
      return `<div class="sys-sec"><div class="k"><span>${esc(c.title.toUpperCase())}</span><em>${c.done} / ${c.total}</em></div>
        ${row("", c.total ? 100 * c.done / c.total : 0, "", "")}<div class="sys-note">${state}${c.updated ? `, last activity ${ago(c.updated)}` : ""}.</div></div>`;
    }).join("");
    $("#sys-body").innerHTML = `<div class="sys-head">${esc(s.headline)}</div>
      ${row("CPU", s.cpu, level(s.cpu, 60, 85), Math.round(s.cpu) + "%")}
      ${row("MEMORY", memPct, level(memPct, 70, 90), gb(used) + " GB")}
      ${s.temp == null ? "" : row("TEMP", s.temp, level(s.temp, 70, 85), Math.round(s.temp) + " &deg;C")}
      <div class="sys-note">${cpuWord}, ${tWord ? tWord + ", " : ""}${memWord}. Model: <b>${s.models.length ? esc(s.models.join(", ")) : "asleep"}</b>.
        ${s.queue ? `<b>${s.queue}</b> answer${s.queue > 1 ? "s" : ""} in line, one is served at a time.` : "Nobody is waiting."}
        Your vault has <b>${s.notes}</b> searchable notes.</div>${cr}`;
  }
  const pop = $("#syspop"), trig = $("#sys-open");
  function openSys() { closeChat(); pop.hidden = false; trig.setAttribute("aria-expanded", "true"); renderSystem(); sysT = setInterval(renderSystem, 4000); }
  function closeSys() { pop.hidden = true; trig.setAttribute("aria-expanded", "false"); clearInterval(sysT); }
  trig.onclick = e => { e.stopPropagation(); pop.hidden ? openSys() : closeSys(); };
  document.addEventListener("click", e => { if (!pop.hidden && !pop.contains(e.target) && !trig.contains(e.target)) closeSys(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") { closeChat(); closeSys(); } });
})();
