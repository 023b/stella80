"use strict";
/* Upload files into a vault: pick the subject first, then add files; they are stored on Stella and queued to be read first. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const DENY = new Set([".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg", ".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".wma", ".opus",
    ".exe", ".dll", ".so", ".dylib", ".pyc", ".class", ".o", ".iso", ".dmg", ".msi", ".tmp", ".part", ".crdownload"]);
  const ext = n => (n.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  const mb = b => (b / 1048576).toFixed(b < 1048576 * 10 ? 1 : 0) + " MB";
  let T = null, files = [], busy = false;

  async function load(vault) {
    T = await (await fetch("api/upload/targets" + (vault ? "?vault=" + encodeURIComponent(vault) : ""))).json();
    render();
  }
  function open(vault) { files = []; $("#upload").hidden = false; $("#upload-body").innerHTML = `<p class="muted">Loading...</p>`; load(vault); }
  function close() { if (!busy) $("#upload").hidden = true; }

  function render() {
    if (!T.allowed) { $("#upload-body").innerHTML = `<p class="muted">${T.why || "Uploads are turned off for your account."}</p>`; return; }
    $("#upload-body").innerHTML = `
      ${T.vaults.length > 1 ? `<label class="fld">Vault <select id="up-vault">${T.vaults.map(v => `<option value="${esc(v.id)}" ${v.id === T.vault ? "selected" : ""}>${esc(v.title)}</option>`).join("")}</select></label>` : `<p class="muted">Adding to the <b>${esc(T.title)}</b>.</p>`}
      <label class="fld">Subject <select id="up-subject">${T.subjects.map(s => `<option value="${esc(s.folder)}">${esc(T.can_new ? s.name + (s.code ? " (" + s.code + ")" : "") : label(s.label))}</option>`).join("")}${T.can_new ? `<option value="__new">New subject...</option>` : ""}</select></label>
      <label class="fld" id="up-new-row" hidden>New subject name <input id="up-new" maxlength="60" placeholder="for example Machine Learning"></label>
      <label class="fld">Name for the note <input id="up-title" maxlength="120" placeholder="optional, for a single file"></label>
      <div class="drop" id="drop" tabindex="0">Drop files here, or click to choose.<br><small>Any file except video and audio. Up to ${T.max_mb} MB each; files over ${T.read_mb} MB are stored but not read.</small>
        <input type="file" id="up-files" multiple hidden></div>
      <ul class="upl" id="upl"></ul>
      <div class="fld"><span class="muted" id="up-msg">Stored on Stella and read first, ahead of the queue.</span><button class="key small" id="up-clear" type="button" hidden>CLEAR DONE</button><button class="key key-orange small" id="up-go" type="button">UPLOAD</button></div>`;
    $("#up-vault")?.addEventListener("change", e => load(e.target.value));
    $("#up-subject").onchange = e => { $("#up-new-row").hidden = e.target.value !== "__new"; };
    const drop = $("#drop"), inp = $("#up-files");
    drop.onclick = () => inp.click();
    drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); add([...e.dataTransfer.files]); };
    inp.onchange = () => { add([...inp.files]); inp.value = ""; };
    $("#up-go").onclick = () => go();
    $("#up-clear").onclick = () => { files = files.filter(x => x.state !== "done"); list(); };
    $("#upl").onclick = e => {
      const b = e.target.closest("button[data-act]");
      if (!b || busy) return;
      const x = files[+b.dataset.i];
      if (!x) return;
      if (b.dataset.act === "drop") { files.splice(+b.dataset.i, 1); list(); }
      else { if (b.dataset.act === "again") x.force = true; go(x); }
    };
    list();
  }
  function add(fs) {
    for (const f of fs) {
      if (DENY.has(ext(f.name))) { toast(`${f.name}: video, audio and programs are not accepted.`, 3500); continue; }
      if (f.size > T.max_mb * 1048576) { toast(`${f.name}: larger than ${T.max_mb} MB.`, 3500); continue; }
      if (!files.some(x => x.file.name === f.name && x.file.size === f.size)) files.push({ file: f, state: "ready", note: "" });
    }
    list();
  }
  function list() {
    $("#upl").innerHTML = files.map((x, i) => `<li><span>${esc(x.file.name)} <span class="muted">${mb(x.file.size)}</span></span><span class="st ${x.state === "done" ? "ok" : x.state === "error" ? "bad" : ""}">${esc(x.note || x.state)}
      ${x.state === "error" ? `<button class="mini" data-act="retry" data-i="${i}" type="button">RETRY</button>` : ""}${x.dup ? `<button class="mini" data-act="again" data-i="${i}" type="button">READ AGAIN</button>` : ""}${x.state !== "uploading" ? `<button class="mini" data-act="drop" data-i="${i}" type="button" title="Remove from this list">&#x2715;</button>` : ""}</span><div class="bar2"><i id="pb${i}" style="width:${x.state === "done" ? 100 : 0}%"></i></div></li>`).join("");
    const bad = files.filter(x => x.state === "error").length, done = files.filter(x => x.state === "done").length, go = $("#up-go");
    if (go) go.textContent = bad && !files.some(x => x.state === "ready") ? `RETRY ${bad} FAILED` : "UPLOAD";
    const cl = $("#up-clear");
    if (cl) cl.hidden = !done;
  }
  function send(x, i, qs) {
    return new Promise(res => {
      const r = new XMLHttpRequest();
      r.open("PUT", "api/upload?" + qs + (x.force ? "&force=1" : "") + "&name=" + encodeURIComponent(x.file.name));
      r.setRequestHeader("Content-Type", "application/octet-stream");
      r.upload.onprogress = e => { const b = $("#pb" + i); if (b && e.lengthComputable) b.style.width = (100 * e.loaded / e.total) + "%"; };
      r.onload = () => {
        let d = {}; try { d = JSON.parse(r.responseText); } catch { }
        if (r.status === 200) { x.state = "done"; x.dup = false; x.force = false; x.note = d.requeued ? "queued to be read again" : d.over_cap ? "stored, too large to read" : "queued first"; }
        else { x.state = "error"; x.note = d.detail || "failed"; x.force = false; x.dup = r.status === 409; }
        res();
      };
      r.onerror = () => { x.state = "error"; x.note = "connection lost"; x.dup = false; res(); };
      r.ontimeout = r.onerror;
      x.state = "uploading"; x.note = "uploading..."; list();
      r.send(x.file);
    });
  }
  async function go(only) {
    const sel = $("#up-subject").value, isNew = sel === "__new";
    const folder = isNew ? $("#up-new").value.trim() : sel;
    const todo = only ? [only] : files.filter(x => x.state !== "done");
    if (!todo.length) { $("#up-msg").textContent = "Add at least one file."; return; }
    if (!folder) { $("#up-msg").textContent = "Choose a subject, or name the new one."; return; }
    busy = true; $("#up-go").disabled = true;
    const title = todo.length === 1 && !only?.force ? $("#up-title").value.trim() : "";
    const qs = `vault=${encodeURIComponent(T.vault)}&folder=${encodeURIComponent(folder)}&new=${isNew ? 1 : 0}` + (title ? "&title=" + encodeURIComponent(title) : "");
    for (const x of todo) { const i = files.indexOf(x); await send(x, i, qs); list(); }
    busy = false; $("#up-go").disabled = false;
    const ok = files.filter(x => x.state === "done").length;
    $("#up-msg").textContent = ok ? `${ok} file${ok === 1 ? "" : "s"} uploaded. They are first in the queue; progress is in the VAULT tab.` : "Nothing was uploaded.";
    if (ok) toast(`${ok} file${ok === 1 ? "" : "s"} uploaded and queued.`, 4000);
    if (isNew) load(T.vault);
  }
  const label = id => { const m = /^([A-Z]{3,4}M?\d{3,4})/.exec(id || ""); return m ? ((typeof SHORT !== "undefined" && SHORT[m[1]]) || m[1]) : String(id || "").replace(/-/g, " "); };
  document.addEventListener("s80-upload", e => open(e.detail?.vault));
  $("#upload-close").onclick = close;
  $("#upload").onclick = e => { if (e.target.id === "upload") close(); };
  document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
})();
