#!/usr/bin/env python3
"""STELLA-80: a local study terminal over the Obsidian vault.

Hybrid retrieval (SQLite FTS5 + nomic-embed vectors) over the vault, answered by a
local Ollama model. Read-only on the vault. Chats live in data/stella80.db.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio, hashlib, hmac, html, json, os, re, secrets, sqlite3, threading, time, urllib.parse, uuid
from pathlib import Path

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = Path(os.environ.get("STELLA80_CONFIG", ROOT.parent / "stella80.yaml")).resolve()
if not CONFIG_FILE.exists():
    raise SystemExit(f"No config at {CONFIG_FILE}. Copy stella80.example.yaml to stella80.yaml and edit it.")
CFG = yaml.safe_load(CONFIG_FILE.read_text()) or {}


def cfg_path(p, base=CONFIG_FILE.parent):
    """Config paths may use ~ and are relative to the config file."""
    p = Path(os.path.expanduser(str(p)))
    return (p if p.is_absolute() else base / p).resolve()


# solo: one person on this machine, no login, localhost only. shared: personal passphrases, admin console, group chat.
MODE = CFG.get("mode", "solo")
SOLO = MODE != "shared"
DATA = cfg_path(CFG.get("data_dir", "data"))
DB = DATA / "stella80.db"
TZ = ZoneInfo(CFG.get("timezone", "UTC"))
# OLLAMA_URL (set by the Docker setup) points both at one instance and wins over the config file
STUDY_OLLAMA = os.environ.get("OLLAMA_URL") or (CFG.get("ollama") or {}).get("study", "http://127.0.0.1:11434")   # answers questions
BG_OLLAMA = os.environ.get("OLLAMA_URL") or (CFG.get("ollama") or {}).get("background", STUDY_OLLAMA)            # embeddings, DEEP, the vault agent
EMBED_MODEL = CFG.get("embed_model", "nomic-embed-text")
RAM_GUARD = CFG.get("ram_guard", True)   # check free RAM before loading a model (only meaningful when Ollama runs on this machine)
SKIP_DIRS = {".obsidian", ".git", ".trash", ".stfolder", ".stversions"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
NUM_CTX = 8192

# Each vault is an Obsidian vault directory; its name is the first path component of everything in the index, so it says
# which vault (and which users) a note belongs to. A vault with an agent_config is fed by the vault agent; one without is
# simply read as it is.
VAULTS = {}
for v in CFG.get("vaults") or []:
    entry = {"title": v.get("title"), "student": v.get("student", "a university student"), "code": v.get("code_hint", ""),
             "agent_cfg": None, "state": None, "src": None}
    if v.get("agent_config"):
        entry["agent_cfg"] = cfg_path(v["agent_config"])
        acfg = yaml.safe_load(entry["agent_cfg"].read_text())
        base = entry["agent_cfg"].parent
        path = cfg_path(acfg["vault_root"], base) / acfg["vault_dir"]
        entry["state"], entry["src"] = cfg_path(acfg["state_dir"], base), cfg_path(acfg["src_root"], base)
    else:
        path = cfg_path(v["path"])
    entry["path"] = path
    entry["title"] = entry["title"] or path.name
    VAULTS[path.name] = entry
AGENT_STATE = {k: v["state"] for k, v in VAULTS.items() if v["state"]}
SRC_ROOTS = {k: v["src"] for k, v in VAULTS.items() if v["src"]}
AGENT_CFG = {k: v["agent_cfg"] for k, v in VAULTS.items() if v["agent_cfg"]}
VAULT_TITLE = {k: v["title"] for k, v in VAULTS.items()}
DEFAULT_VAULT = next(iter(VAULTS), "")

# The three engine slots on the front panel. `host` picks the Ollama instance: study or background.
ENGINE_DEFAULTS = {
    "quick": {"label": "QUICK", "model": "qwen3.5:4b", "host": "study", "vision": True,
              "blurb": "Small and fast. Lookups, definitions, flashcards."},
    "scholar": {"label": "SCHOLAR", "model": "gemma4:26b-a4b-it-qat", "host": "study", "vision": True,
                "blurb": "The all-rounder. Reads images."},
    "deep": {"label": "DEEP", "model": "qwen3:30b-a3b", "host": "background", "vision": False,
             "blurb": "Slower start, strongest at maths with THINK on."},
}
MODELS = [{"id": k, **d, **(CFG.get("engines", {}).get(k) or {})} for k, d in ENGINE_DEFAULTS.items()]
ENGINE_IDS = [m["id"] for m in MODELS]

MODES = {
    "teach": ("TEACH", "Teach the concept from scratch: intuition first, then the formal definition and maths, then a small worked example, then common pitfalls. Assume nothing; define every term before using it."),
    "exam": ("EXAM", "Write the answer the way a top-mark university exam answer would look: direct, structured, every key point a marker would tick, equations where they earn marks, a labelled diagram (mermaid) if the topic is usually drawn. End with a one-line 'Marker's checklist'."),
    "derive": ("DERIVE", "Derive or work through the maths step by step. Number each step, state what rule is used at each step, keep all equations in LaTeX. Finish with the boxed final result and what each symbol means."),
    "quiz": ("QUIZ", "Do not explain. Write 5 exam-style questions on the topic, increasing in difficulty (recall, understand, apply, analyse, exam-length). After each question put the model answer inside <details><summary>Answer</summary> ... </details> so the student can test themselves first."),
    "cards": ("CARDS", "Make 8 to 12 revision flashcards. Output ONLY one fenced block with language `cards`, one card per two lines: a line starting 'Q: ' then a line starting 'A: '. Answers short (one or two lines), maths in LaTeX."),
    "code": ("CODE", "Give one minimal runnable Python script that demonstrates the concept (use numpy/opencv/matplotlib, or ROS 2 rclpy if it is a ROS topic). Then 2-3 lines on what to change and observe."),
    "simple": ("SIMPLE", "Explain in plain words like to a smart friend outside the field, with an everyday analogy. Then one short paragraph with the proper terminology so it maps back to the lecture."),
    "brief": ("BRIEF", "Answer in at most 5 lines. No preamble."),
}

DEPTH = {"brief": (5, 7000, 1000), "normal": (8, 11000, 2200), "deep": (12, 15000, 3600)}  # chunks, ctx chars, num_predict

VISUALS_GUIDE = r"""
VISUAL TOOLS (use when they genuinely help understanding, never as decoration):
- Maths: inline $...$ and display $$...$$ LaTeX. Matrices with \begin{bmatrix}.
- Function plot: a fenced block with language `plot` holding JSON, e.g.
```plot
{"title": "Gaussian kernels", "x": [-4, 4], "fns": ["exp(-x^2/2)", "exp(-x^2/8)/2"], "labels": ["sigma=1", "sigma=2"]}
```
  Keys: title, x [min,max], optional y [min,max], fns (math.js syntax in x), labels, optional points {"x":[...],"y":[...],"label":"..."}.
  For a 3D surface use {"title": "...", "surface": "expression in x and y", "x": [min,max], "y": [min,max]}.
- Diagram: a fenced block with language `mermaid` (flowchart TD, sequenceDiagram, stateDiagram-v2). Keep node labels short and quote labels that contain symbols, e.g. A["x_k = F x_k-1"].
- Tables in markdown when comparing methods.
- Mermaid flowcharts: only nodes and arrows, edge labels as A -->|label| B (one pair of bars). No "Note over" lines (those are for sequenceDiagram only).
- A real block is the only way anything gets drawn. Never write a placeholder such as "[drawing mermaid ...]" or "see the diagram below" without the block itself.
  Time curves can be plotted directly, e.g. a rest-to-rest cubic from 0 to 1 over 0..1 s: {"title": "Cubic trajectory", "x": [0, 1], "fns": ["3*x^2-2*x^3", "6*x-6*x^2", "6-12*x"], "labels": ["position", "velocity", "acceleration"]}
"""

DRAW_RE = re.compile(r"\b(draw|plot|graph|chart|diagram|sketch|visuali[sz]e|illustrate|flowchart|show me (?:a|the) (?:curve|figure|picture))\b", re.I)
DRAW_NOTE = ("\n\nThe student wants a picture. Draw it yourself now with one fenced `plot` block (a curve or surface from a formula) or one fenced `mermaid` block "
             "(a flow, structure or sequence). The excerpts do not need to contain a figure, and the vault-only rule covers facts, not drawing: take the formula or the "
             "steps from the excerpts or from standard theory and draw those. Put the block on its own lines, then two or three sentences on how to read it. "
             "Do not write a placeholder instead of the block, and do not say that no graph was provided.")


# ---------------------------------------------------------------- database

class ClosingConn(sqlite3.Connection):
    """'with db() as c' commits as usual and then also closes, so connections are not left open."""
    def __exit__(self, *a):
        try:
            return super().__exit__(*a)
        finally:
            self.close()


def db():
    c = sqlite3.connect(DB, timeout=30, factory=ClosingConn)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL);
        CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, path TEXT, subject TEXT, kind TEXT,
            title TEXT, heading TEXT, text TEXT, images TEXT, emb BLOB);
        CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(title, heading, text, content='chunks',
            content_rowid='id', tokenize='porter unicode61');
        CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY, title TEXT, subject TEXT, created REAL, updated REAL, pinned INTEGER DEFAULT 0, owner TEXT);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, chat_id TEXT, role TEXT, content TEXT,
            thinking TEXT, sources TEXT, meta TEXT, created REAL);
        CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id);
        CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY, display TEXT, vault TEXT, role TEXT, color TEXT,
            profile TEXT, salt TEXT, hash TEXT, last_seen REAL DEFAULT 0, prefs TEXT DEFAULT '{}', limits TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS room(id INTEGER PRIMARY KEY, user TEXT, text TEXT, created REAL, vault TEXT);
        CREATE TABLE IF NOT EXISTS study_events(id INTEGER PRIMARY KEY, user TEXT, ts REAL, day TEXT, subject TEXT, mode TEXT, question TEXT, topics TEXT);
        CREATE INDEX IF NOT EXISTS study_events_user_day ON study_events(user, day);
        CREATE TABLE IF NOT EXISTS study_time(user TEXT, day TEXT, seconds INTEGER DEFAULT 0, PRIMARY KEY(user, day));
        CREATE TABLE IF NOT EXISTS study_days(user TEXT, day TEXT, summary TEXT, sum_q INTEGER, updated REAL, llm INTEGER DEFAULT 0, PRIMARY KEY(user, day));
        """)
        c.execute("CREATE TABLE IF NOT EXISTS doc_alerts(vault TEXT, path TEXT, sha TEXT, kind TEXT, announced REAL, resolved REAL, PRIMARY KEY(vault, path))")
        # room.vault: NULL = everyone, otherwise only that vault's users (and the admin)
        seed_users(c)
        if not c.execute("SELECT 1 FROM users WHERE name='stella'").fetchone():   # the voice of the system in the group chat; cannot log in
            c.execute("INSERT INTO users(name,display,vault,role,color,profile) VALUES('stella','Stella','','system','#ffb547','')")


# ---------------------------------------------------------------- vault

def vault_dirs():
    return [(v["path"].parent, v["path"]) for v in VAULTS.values() if v["path"].is_dir()]


def vault_resolve(rel):
    """Absolute path for an index path like 'My-vault/Machine-Learning/x.md', or None."""
    v = VAULTS.get(rel.split("/")[0])
    if not v:
        return None
    p = (v["path"].parent / rel).resolve()
    return p if v["path"] in p.parents else None


def subjects():
    """Module folders are <vault>/<CODE>-<Name> (for example COMP3010-ComputerVision) or <vault>/<Module-Name>."""
    out = []
    for _parent, vault in vault_dirs():
        for d in sorted(vault.iterdir()):
            if not d.is_dir() or d.name.startswith((".", "_")) or d.name.startswith("00-"):
                continue
            m = re.match(r"^([A-Z]{3,4}M?\d{3,4})-(.+)$", d.name)
            if m:
                out.append({"id": m.group(1), "folder": d.name, "vault": vault.name, "_path": d,
                            "name": re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", m.group(2))})
            else:
                out.append({"id": d.name, "folder": d.name, "vault": vault.name, "_path": d, "name": d.name.replace("-", " ")})
        if vault.name in AGENT_CFG:   # courses the agent created from the source folders, before any note folder exists
            try:
                for folder, tup in agent_courses(vault.name)[0].items():
                    if not any(x["id"] == tup[0] and x["vault"] == vault.name for x in out):
                        out.append({"id": tup[0], "folder": tup[0], "vault": vault.name, "_path": vault, "name": folder.replace("-", " ").replace("_", " ")})
            except Exception:
                pass
    return out


def kind_of(rel):
    parts = rel.split("/")
    if "_raw" in parts:
        return "raw"
    if "_auto" in parts:
        return "processed"
    return "human"


def subject_of(rel, text, codes):
    hits = [c for c in codes if f"/{c}-" in "/" + rel or f"/{c}/" in "/" + rel]
    if not hits:
        m = re.search(r"^courses:\s*\[([^\]]*)\]", text[:600], re.M) or re.search(r"^course:\s*(\S+)", text[:600], re.M)
        if m:
            hits = [c for c in codes if c.lower() in m.group(1).lower()]
    return "," + ",".join(hits) + "," if hits else ",GENERAL,"


FRONT = re.compile(r"\A---\n.*?\n---\n", re.S)
NOT_SOURCES = re.compile(r"/_auto/(Status\.md|Knowledge graph\.md|maps/)")
HUB_STARTER = "The agent links here but never edits this note."   # course hubs the agent creates; indexed once you write in them
USER_MARK = re.compile(r"<!-- user-notes: anything below this line is preserved -->\n?")
HEADING = re.compile(r"^(#{1,4})\s+(.+)$", re.M)
EMBED = re.compile(r"!\[\[([^\]|]+?\.(?:png|jpe?g|gif|webp|svg))(?:\|[^\]]*)?\]\]", re.I)
WIKILINK = re.compile(r"(?<!!)\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]")   # [[path|label]] -> label; image embeds are kept
AGENT_HEAD = re.compile(r"^(Course hub|Source file|Raw text \(no AI\)|Hub): .*\n?", re.M)   # link lines atop generated notes


def chunk_markdown(text, max_chars=1400):
    """Split on headings, then pack paragraphs (never splitting inside a code fence) up to max_chars."""
    text = WIKILINK.sub(r"\1", AGENT_HEAD.sub("", USER_MARK.sub("", FRONT.sub("", text))))
    sections, last, head = [], 0, ""
    for m in HEADING.finditer(text):
        if m.start() > last:
            sections.append((head, text[last:m.start()]))
        head, last = m.group(2).strip(), m.end()
    sections.append((head, text[last:]))
    out = []
    for head, body in sections:
        blocks, buf, in_fence = [], [], False
        for line in body.split("\n"):
            if line.strip().startswith("```"):
                in_fence = not in_fence
            buf.append(line)
            if not in_fence and not line.strip():
                blocks.append("\n".join(buf)); buf = []
        if buf:
            blocks.append("\n".join(buf))
        cur = ""
        for b in blocks:
            if cur and len(cur) + len(b) > max_chars:
                out.append((head, cur.strip())); cur = ""
            while len(b) > max_chars * 2:   # very long code fence or raw dump
                out.append((head, b[:max_chars].strip())); b = b[max_chars - 200:]
            cur += b + "\n"
        if cur.strip():
            out.append((head, cur.strip()))
    return [(h, t) for h, t in out if len(t) > 40]


index_state = {"running": False, "done": 0, "total": 0, "last": None, "error": None}
index_lock = threading.Lock()


def embed(texts, prefix):
    r = httpx.post(f"{BG_OLLAMA}/api/embed", json={"model": EMBED_MODEL, "input": [prefix + t[:6000] for t in texts],
                                                   "keep_alive": "2m"}, timeout=300)
    r.raise_for_status()
    v = np.array(r.json()["embeddings"], dtype=np.float32)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


def reindex():
    if not index_lock.acquire(blocking=False):
        return
    try:
        index_state.update(running=True, error=None, done=0)
        codes = [s["id"] for s in subjects()]
        c = db()
        known = {r["path"]: r["mtime"] for r in c.execute("SELECT path, mtime FROM files")}
        todo, seen = [], set()
        for parent, vdir in vault_dirs():
            for p in vdir.rglob("*.md"):
                rel = p.relative_to(parent).as_posix()
                if any(part in SKIP_DIRS or part.startswith(".") for part in p.relative_to(parent).parts):
                    continue
                if NOT_SOURCES.search(rel):   # the agent's status page and link maps: no content worth citing
                    continue
                seen.add(rel)
                if known.get(rel) != p.stat().st_mtime:
                    todo.append((parent, p))
        for rel in set(known) - seen:
            drop_file(c, rel)
        c.commit()
        index_state["total"] = len(todo)
        index_state["changed"] = len(todo)
        for parent, p in todo:
            rel = p.relative_to(parent).as_posix()
            text = p.read_text(errors="ignore")
            subj, kind, title = subject_of(rel, text, codes), kind_of(rel), p.stem
            pieces = [] if HUB_STARTER in text and len(text) < 600 else chunk_markdown(text)
            embs = embed([f"{title} > {h}\n{t}" for h, t in pieces], "search_document: ") if pieces else []
            drop_file(c, rel)
            for (h, t), e in zip(pieces, embs):
                imgs = json.dumps(EMBED.findall(t))
                cur = c.execute("INSERT INTO chunks(path,subject,kind,title,heading,text,images,emb) VALUES(?,?,?,?,?,?,?,?)",
                                (rel, subj, kind, title, h, t, imgs, e.tobytes()))
                c.execute("INSERT INTO fts(rowid,title,heading,text) VALUES(?,?,?,?)", (cur.lastrowid, title, h, t))
            c.execute("INSERT OR REPLACE INTO files VALUES(?,?)", (rel, p.stat().st_mtime))
            c.commit()
            index_state["done"] += 1
        c.close()
        load_vectors()
        index_state["last"] = time.time()
    except Exception as e:  # surfaced on the status lamp
        index_state["error"] = str(e)
    finally:
        index_state["running"] = False
        index_lock.release()


def drop_file(c, rel):
    for r in c.execute("SELECT id, title, heading, text FROM chunks WHERE path=?", (rel,)).fetchall():
        c.execute("INSERT INTO fts(fts,rowid,title,heading,text) VALUES('delete',?,?,?,?)", (r["id"], r["title"], r["heading"], r["text"]))
    c.execute("DELETE FROM chunks WHERE path=?", (rel,))
    c.execute("DELETE FROM files WHERE path=?", (rel,))


VEC = {"ids": np.zeros(0, dtype=np.int64), "m": np.zeros((0, 768), dtype=np.float32), "subj": [], "kind": [], "path": []}


def load_vectors():
    with db() as c:
        rows = c.execute("SELECT id, emb, subject, kind, path FROM chunks").fetchall()
    VEC["ids"] = np.array([r["id"] for r in rows], dtype=np.int64)
    VEC["m"] = np.stack([np.frombuffer(r["emb"], dtype=np.float32) for r in rows]) if rows else np.zeros((0, 768), np.float32)
    VEC["subj"] = np.array([r["subject"] for r in rows])
    VEC["kind"] = np.array([r["kind"] for r in rows])
    VEC["path"] = np.array([r["path"] for r in rows])


KIND_W = {"human": 1.0, "processed": 0.85, "raw": 0.75}


def fts_query(q):
    words = [w for w in re.findall(r"[A-Za-z0-9]{2,}", q.lower()) if w not in STOP]
    return " OR ".join(f'"{w}"' for w in words[:24])


STOP = set("the a an and or of to in on for is are was be it this that with what how why when which who does do can i me my you your explain about give tell show from as at by into vs".split())


def retrieve(query, subject, k, vault, mode="hybrid"):
    """Reciprocal-rank fusion of BM25 and cosine similarity, weighted toward your own notes.
    mode "keyword" or "embeddings" uses one ranker alone (for the evaluation in scripts/eval.py)."""
    scores = {}
    subj_like = f"%,{subject},%" if subject and subject != "ALL" else "%"
    path_like = vault + "/%"
    q = fts_query(query)
    with db() as c:
        if q and mode != "embeddings":
            rows = c.execute("SELECT chunks.id FROM fts JOIN chunks ON chunks.id=fts.rowid WHERE fts MATCH ? AND chunks.subject LIKE ? AND chunks.path LIKE ? "
                             "ORDER BY bm25(fts, 4.0, 2.0, 1.0) LIMIT 40", (q, subj_like, path_like)).fetchall()
            for rank, r in enumerate(rows):
                scores[r["id"]] = scores.get(r["id"], 0) + 1 / (60 + rank)
        if len(VEC["ids"]) and mode != "keyword":
            qv = embed([query], "search_query: ")[0]
            sims = VEC["m"] @ qv
            sims = np.where(np.char.startswith(VEC["path"].astype(str), vault + "/"), sims, -1)
            if subject and subject != "ALL":
                sims = np.where(np.char.find(VEC["subj"].astype(str), f",{subject},") >= 0, sims, -1)
            for rank, i in enumerate(np.argsort(-sims)[:40]):
                if sims[i] > 0:
                    cid = int(VEC["ids"][i])
                    scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
        if not scores:
            return []
        rows = {r["id"]: dict(r) for r in c.execute(
            f"SELECT id, path, subject, kind, title, heading, text, images FROM chunks WHERE id IN ({','.join('?' * len(scores))})",
            list(scores))}
    ranked = sorted(rows.values(), key=lambda r: -scores[r["id"]] * KIND_W.get(r["kind"], 1))
    out, per_file = [], {}
    for r in ranked:   # at most 3 chunks from any one file, so one long lecture dump can't drown the rest
        if per_file.get(r["path"], 0) >= 3:
            continue
        per_file[r["path"]] = per_file.get(r["path"], 0) + 1
        r["images"] = json.loads(r["images"] or "[]")
        out.append(r)
        if len(out) >= k:
            break
    return out


# ---------------------------------------------------------------- ollama

def host_url(h):
    return BG_OLLAMA if h == "background" else STUDY_OLLAMA


async def ollama_get(client, host, path):
    try:
        r = await client.get(f"{host}{path}", timeout=4)
        return r.json()
    except Exception:
        return None


def mem_available():
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) * 1024
    except OSError:   # not Linux: no way to tell, so never hold a model back
        pass
    return 1 << 50


async def model_status():
    async with httpx.AsyncClient() as client:
        tags = {h: await ollama_get(client, host_url(h), "/api/tags") for h in ("study", "background")}
        ps = {h: await ollama_get(client, host_url(h), "/api/ps") for h in ("study", "background")}
    out = []
    for m in MODELS:
        t = tags[m["host"]]
        sizes = {x["name"]: x["size"] for x in (t or {}).get("models", [])}
        if m["model"] not in sizes and ":" not in m["model"] and f"{m['model']}:latest" in sizes:
            m = {**m, "model": m["model"] + ":latest"}   # Ollama lists untagged pulls as name:latest
        loaded = any(x["name"] == m["model"] for x in (ps[m["host"]] or {}).get("models", []))
        out.append({**m, "online": t is not None, "installed": m["model"] in sizes,
                    "size": sizes.get(m["model"], 0), "loaded": loaded})
    return out


cpu_used = {}   # models this app loaded on the shared background instance -> last use
queue = []      # answers waiting for or using the model, first in first out: [{"user": name}]


async def pick_model(preset_id):
    """Honour the chosen preset unless loading it would push the machine into swap; then drop to QUICK."""
    status = await model_status()
    want = next((m for m in status if m["id"] == preset_id), status[1])
    if want["online"] and want["installed"]:
        need = want["size"] * 1.05 + 1.0e9
        if want["loaded"] or not RAM_GUARD or mem_available() > need:
            return want, None
        # Free whatever this app itself loaded before giving up. On the background instance only
        # unload a model we used recently, so the vault agent's own sessions are left alone.
        async with httpx.AsyncClient() as client:
            ps = await ollama_get(client, STUDY_OLLAMA, "/api/ps") or {}
            victims = [(STUDY_OLLAMA, x["name"]) for x in ps.get("models", []) if BG_OLLAMA != STUDY_OLLAMA or x["name"] != EMBED_MODEL]
            victims += [(BG_OLLAMA, name) for name, t in cpu_used.items() if time.time() - t < 900 and name != want["model"]]
            for host, name in victims:
                await client.post(f"{host}/api/generate", json={"model": name, "keep_alive": 0}, timeout=30)
                cpu_used.pop(name, None)
        await asyncio.sleep(2)
        if mem_available() > need:
            return want, None
    quick = status[0]
    why = (f"{want['label']} needs ~{want['size'] / 1e9:.0f} GB free and this machine only has {mem_available() / 1e9:.1f} GB right now"
           if want["online"] else f"{want['label']}'s Ollama instance is offline")
    return quick, why + ", so this answer comes from QUICK."


def system_prompt(subject_name, mode, grounding, visuals, profile, persona=""):
    p = [
        f"You are STELLA-80, a study tutor for {profile['who']}.",
        f"Current module: {subject_name}." if subject_name else "The student may ask about any of their modules.",
        "You are given numbered excerpts from the student's own Obsidian vault (their notes, lecture extractions, past exams).",
    ]
    if grounding == "strict":
        p.append("Answer ONLY from the excerpts. If they do not contain the answer, say so plainly and say what is missing. Cite excerpts as [1], [2] after the sentence they support.")
    else:
        p.append("Prefer the excerpts and cite them as [1], [2]. You may add standard textbook knowledge beyond them; mark such parts with (general) so the student knows it is not from their lecture material. Where the lecture's notation or framing differs from the textbook, follow the lecture's.")
    p.append("Task style: " + (profile["code"] if mode == "code" else MODES.get(mode, MODES["teach"])[1]))
    p.append("Write like good handwritten study notes: plain headings, no emoji, no filler, no 'Great question'.")
    if persona:
        p.append("Tone and manner this student has asked for (it never overrides the rules about accuracy and citing the excerpts): " + persona)
    if visuals:
        p.append(VISUALS_GUIDE)
    else:
        p.append("Use LaTeX ($...$, $$...$$) for maths. Do not produce plot or mermaid blocks.")
    return "\n".join(p)


# ---------------------------------------------------------------- app

app = FastAPI(title="STELLA-80")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

# ---------------------------------------------------------------- accounts and login
# Shared mode: everyone logs in with a name and a personal passphrase. The session cookie carries the user name and is
# signed with a key in <data_dir>/secret.json. Each user belongs to one vault; searches, notes, images and chats are
# scoped to it. Solo mode skips all of this and everything belongs to one local user.
SECRET_FILE = DATA / "secret.json"
COOKIE = "s80_session"
SESSION_DAYS = 30
login_fails = {}
presence = {}      # name -> {"t": last heartbeat, "doing": "idle" | "asking" | "away"}
CODE_HINT = ("Give one minimal runnable Python script that demonstrates the concept (use numpy, pandas, scikit-learn, matplotlib "
             "or PyTorch as fits the topic). Then 2-3 lines on what to change and observe.")
PROFILE = {k: {"who": v["student"], "code": v["code"] or CODE_HINT} for k, v in VAULTS.items()}
SOLO_USER = re.sub(r"[^a-z0-9_-]", "", str(CFG.get("solo_user", "me")).lower()) or "me"
ADMIN_WORD = CFG.get("admin_contact", "your admin")   # how messages refer to the person who runs this machine


def secrets_cfg():
    if not SECRET_FILE.exists():   # first start: a fresh signing key, readable only by this user
        DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
        SECRET_FILE.write_text(json.dumps({"key": secrets.token_hex(32)}))
        SECRET_FILE.chmod(0o600)
    return json.loads(SECRET_FILE.read_text())


def seed_users(c):
    """Solo mode: one admin account that never needs a passphrase. Shared mode: accounts come from manage.py."""
    if not SOLO:   # moved from solo to shared: the passwordless account is locked until manage.py passwd gives it a passphrase
        c.execute("UPDATE users SET salt=NULL, hash=NULL WHERE hash='!'")
    if SOLO and not c.execute("SELECT 1 FROM users WHERE name=?", (SOLO_USER,)).fetchone():
        c.execute("INSERT INTO users(name,display,vault,role,color,profile,salt,hash) VALUES(?,?,?,?,?,?,?,?)",
                  (SOLO_USER, SOLO_USER.capitalize(), DEFAULT_VAULT, "admin", "#e0a526", DEFAULT_VAULT, "-", "!"))


def hash_pw(pw, salt_hex):
    return hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex), n=2 ** 14, r=8, p=1).hex()


def sign(name, exp, salt):
    """The passphrase salt is part of the signature, so a new passphrase or a removed account ends every old session."""
    key = bytes.fromhex(secrets_cfg()["key"])
    return f"{name}.{exp}.{hmac.new(key, f'{name}.{exp}.{salt}'.encode(), hashlib.sha256).hexdigest()}"


def user_from_cookie(val):
    try:
        name, exp, _ = val.split(".", 2)
        if int(exp) <= time.time():
            return None
        with db() as c:
            r = c.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
        if not r or not r["hash"] or not hmac.compare_digest(sign(name, int(exp), r["salt"]), val):
            return None
        u = dict(r)
        return None if u and limits_of(u)["locked"] else u
    except Exception:
        return None


def me(request: Request):
    return request.state.user


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    if path in ("/login", "/logout") or path.startswith("/static/fonts/"):
        return await call_next(request)
    if path.startswith("/api/bot/"):   # a chat bot or script on this machine: shared secret in <data_dir>/bot-token, acts as the admin
        f = DATA / "bot-token"
        want = f.read_text().strip() if f.exists() else ""
        if not want or not hmac.compare_digest(request.headers.get("x-bot-token", ""), want):
            return HTMLResponse("bad token", status_code=401)
        with db() as c:
            request.state.user = dict(c.execute("SELECT * FROM users WHERE role='admin' ORDER BY name LIMIT 1").fetchone())
        return await call_next(request)
    if SOLO:
        with db() as c:
            request.state.user = dict(c.execute("SELECT * FROM users WHERE name=?", (SOLO_USER,)).fetchone())
        return await call_next(request)
    user = user_from_cookie(request.cookies.get(COOKIE, ""))
    if not user:
        if path.startswith("/api/") or path.startswith("/vault/"):
            return HTMLResponse("login required", status_code=401)
        return RedirectResponse("login", status_code=303)   # relative, so it works under a path prefix
    request.state.user = user
    return await call_next(request)


LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>STELLA-80</title><style>
@font-face{font-family:VT323;src:url(static/fonts/VT323-Regular.ttf)}@font-face{font-family:Righteous;src:url(static/fonts/Righteous-Regular.ttf)}
body{margin:0;min-height:100dvh;display:grid;place-items:center;background:#000;font-family:VT323,monospace}
.box{width:min(400px,calc(100vw - 32px));background:#0b0b0b;border:1px solid #222;border-radius:12px;padding:22px}
h1{font-family:Righteous,sans-serif;margin:0 0 4px;font-size:30px;color:#e9e4d8}h1 span{color:#c8561e}
.st{display:flex;flex-direction:column;gap:3px;margin:8px 0 16px}.st i{height:4px}
.scr{background:#000;border:1px solid #1c1c1c;border-radius:8px;padding:16px;color:#ffb547;font-size:20px;text-shadow:0 0 5px rgba(255,170,40,.3)}
.who{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:10px 0}
.who label{cursor:pointer}.who input{position:absolute;opacity:0}
.who span{display:block;text-align:center;padding:8px 4px;border:1px solid #2a2a2a;border-radius:6px;color:#bdb6a4;font-size:22px}
.who input:checked+span{border-color:var(--c);color:#fff;background:#141414;box-shadow:0 0 0 1px var(--c)}
.who input:focus-visible+span{outline:2px solid #ffb547}
input[type=password]{width:100%;box-sizing:border-box;margin:8px 0 12px;background:#000;border:1px solid #3a2a10;color:#ffd68a;font:inherit;padding:8px}
button{background:#e07a24;color:#fff6e6;border:1px solid #0005;border-radius:5px;font:inherit;padding:6px 20px;box-shadow:0 3px 0 #8f4512;cursor:pointer}
.err{color:#ff6a4a}small{color:#8a8478;font-size:16px;display:block;margin-top:10px}</style></head><body><form class="box" method="post" action="login">
<h1>STELLA<span>-</span>80</h1><div class="st"><i style="background:#5b3a1e"></i><i style="background:#c8561e"></i><i style="background:#e0a526"></i><i style="background:#7a8b2c"></i></div>
<div class="scr">WHO IS THIS?<div class="who">%USERS%</div>PASSPHRASE.%ERR%<input type="password" name="pw" autocomplete="current-password"><button>LOG IN</button>
<small>Your passphrase is personal. Ask %ADMIN% if you have lost it.</small></div>
</form></body></html>"""


def login_page(err="", chosen=""):
    with db() as c:
        users = c.execute("SELECT name, display, color FROM users WHERE hash IS NOT NULL ORDER BY (role='admin') DESC, display").fetchall()
    if not users:
        return LOGIN_PAGE.replace("%USERS%", "").replace("%ADMIN%", html.escape(ADMIN_WORD)).replace(
            "%ERR%", '<br><span class="err">NO ACCOUNTS YET. ON THE SERVER RUN: python stella80/manage.py add NAME --admin</span>')
    chips = "".join(f'<label style="--c:{html.escape(u["color"])}"><input type="radio" name="name" value="{html.escape(u["name"])}" '
                    f'{"checked" if u["name"] == chosen or (not chosen and i == 0) else ""} required><span>{html.escape(u["display"])}</span></label>'
                    for i, u in enumerate(users))
    return LOGIN_PAGE.replace("%USERS%", chips).replace("%ADMIN%", html.escape(ADMIN_WORD)).replace("%ERR%", f'<br><span class="err">{err}</span>' if err else "")


@app.get("/login", response_class=HTMLResponse)
def login_get():
    if SOLO:
        return RedirectResponse("./", status_code=303)
    return login_page()


@app.get("/logout")
def logout():
    r = RedirectResponse("login", status_code=303)
    r.delete_cookie(COOKIE)
    return r


@app.post("/login")
async def login(request: Request):
    if SOLO:
        return RedirectResponse("./", status_code=303)
    ip = (request.headers.get(CFG["trust_ip_header"]) if CFG.get("trust_ip_header") else None) or request.client.host
    fails = [t for t in login_fails.get(ip, []) if time.time() - t < 900]
    if len(fails) >= 8:
        return HTMLResponse(login_page("TOO MANY TRIES. WAIT 15 MIN."), status_code=429)
    form = urllib.parse.parse_qs((await request.body()).decode())
    name, pw = form.get("name", [""])[0], form.get("pw", [""])[0]
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
    ok = bool(u and u["hash"] and hmac.compare_digest(hash_pw(pw, u["salt"]), u["hash"]))
    if ok and limits_of(dict(u))["locked"]:
        return HTMLResponse(login_page(f"ACCOUNT LOCKED. ASK {html.escape(ADMIN_WORD.upper())}.", name), status_code=403)
    if not ok:
        login_fails[ip] = fails + [time.time()]
        await asyncio.sleep(1.5)
        return HTMLResponse(login_page("?SYNTAX ERROR. TRY AGAIN.", name), status_code=401)
    login_fails.pop(ip, None)
    r = RedirectResponse("./", status_code=303)
    exp = int(time.time()) + SESSION_DAYS * 86400
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    r.set_cookie(COOKIE, sign(name, exp, u["salt"]), max_age=SESSION_DAYS * 86400, httponly=True, secure=secure, samesite="lax")
    return r


@app.on_event("startup")
async def startup():
    secrets_cfg()
    init_db()
    load_vectors()
    threading.Thread(target=reindex, daemon=True).start()
    asyncio.create_task(summary_loop())
    asyncio.create_task(doc_watch_loop())

    async def periodic():   # notes edited in Obsidian or arriving by sync; pick them up
        while True:
            await asyncio.sleep(600)
            threading.Thread(target=reindex, daemon=True).start()
    asyncio.create_task(periodic())


@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "static" / "index.html").read_text()


@app.get("/api/me")
def api_me(request: Request):
    u = me(request)
    return {"name": u["name"], "display": u["display"], "vault": u["vault"], "role": u["role"], "color": u["color"],
            "vault_title": VAULT_TITLE.get(u["vault"], u["vault"]), "prefs": prefs_of(u), "palette": PALETTE,
            "limits": limits_of(u), "personas": {k: v[0] for k, v in PERSONAS.items()}, "mode": MODE, "admin_word": ADMIN_WORD}


def suggested_topics(vault, course, name=""):
    """Starter questions for a subject with no hand-made topic notes: its most mentioned concepts, else its document titles."""
    try:
        c = agent_db(vault)
        rows = c.execute("SELECT c.name, COUNT(DISTINCT m.doc_id) n, COUNT(*) k FROM concepts c JOIN mentions m ON m.concept_id=c.id "
                         "JOIN docs d ON d.id=m.doc_id WHERE json_extract(d.course,'$[0]')=? GROUP BY c.id ORDER BY n DESC, k DESC LIMIT 14", (course,)).fetchall()
        out = [r["name"] for r in rows]
        if len(out) < 6:
            for r in c.execute("SELECT title FROM docs WHERE kind='doc' AND json_extract(course,'$[0]')=? AND status IN ('done','pending') ORDER BY id LIMIT 24", (course,)):
                t = re.sub(r"\b[A-Z]{3,5}\d{3,4}[A-Z]?\b|\(|\)|\bCopy\b|\d{6,}|\s+-\s+\d{1,2}:?\d{2}\s*[AP]M", " ", r["title"])
                t = re.sub(r"\s+", " ", t).strip(" -,_")
                noisy = re.search(r"(?i)week|lecture|\blab\b|exam|sem(ester|r)|\d{4}|\b3pp\b|assign|publish|sheet|solution|coupon|copy|\.html", t)
                if 8 < len(t) < 60 and not noisy and t not in out:
                    out.append(t)
        c.close()
        if len(out) < 4 and name:   # nothing better yet: clean generic starters until concepts have been found
            out += [f"an overview of {name}", f"the main ideas in {name}", f"what to revise first in {name}"]
        return out[:14]
    except Exception:
        return []


def latest_ideas(vault, sid, limit=8):
    """Ready-made questions from the newest processed files of a subject, built from the concepts found in them (no model needed)."""
    try:
        c = agent_db(vault)
        docs = []
        for d in c.execute("SELECT id, path, title, course FROM docs WHERE kind='doc' AND status='done'"):
            c0 = json.loads(d["course"])[0]
            if c0 == sid or c0.startswith(sid + "-"):
                try:
                    docs.append((Path(d["path"]).stat().st_mtime, d))
                except OSError:
                    pass
        docs.sort(key=lambda x: -x[0])
        per = []
        for mt, d in docs[:8]:
            rows = c.execute("SELECT c.name, c.kind, COUNT(*) k FROM concepts c JOIN mentions m ON m.concept_id=c.id WHERE m.doc_id=? "
                             "GROUP BY c.id ORDER BY k DESC, length(c.name) LIMIT 4", (d["id"],)).fetchall()
            if rows:
                t = re.sub(r"\s+", " ", re.sub(r"\s*-\s*Copy\b|\.(pdf|pptx|docx|md)$", "", d["title"], flags=re.I)).strip()
                per.append((mt, t[:60], [(r["name"], r["kind"]) for r in rows]))
        c.close()
        out, seen = [], set()
        for i in range(4):   # round robin over the newest files so one file does not take every slot
            for mt, title, cs in per:
                if i < len(cs) and cs[i][0] not in seen and len(out) < limit:
                    name, kind = cs[i]
                    seen.add(name)
                    if i == 1 and len(cs) > 2:
                        q = f"What is the difference between {name} and {cs[2][0]}?"
                    elif kind in ("algorithm", "method"):
                        q = f"How does {name} work, step by step?"
                    elif kind in ("metric", "model", "tool", "standard"):
                        q = f"When would I use {name}, and what are its limits?"
                    elif i == 2:
                        q = f"Give me a likely exam question on {name}, with a model answer."
                    else:
                        q = f"What is {name}, and why does it matter?"
                    out.append({"q": q, "src": title, "fresh": time.time() - mt < 86400})
        return out
    except Exception:
        return []


_codes = {"t": 0.0, "v": {}}


def course_codes(vault):
    """Module code per course (for example COMM034), taken from the most common code in that course's file names."""
    if time.time() - _codes["t"] < 300 and vault in _codes["v"]:
        return _codes["v"][vault]
    out = {}
    try:
        c = agent_db(vault)
        tally = {}
        for r in c.execute("SELECT title, path, course FROM docs WHERE kind='doc'"):
            course = json.loads(r["course"])[0]
            for m in re.findall(r"(?<![A-Za-z0-9])([A-Z]{3,5}\d{3,4}[A-Z]?)(?![A-Za-z0-9])", f"{r['title']} {Path(r['path']).name}"):
                tally.setdefault(course, {}).setdefault(m, 0)
                tally[course][m] += 1
        c.close()
        out = {k: max(v, key=v.get) for k, v in tally.items()}
    except Exception:
        pass
    _codes["v"][vault] = out
    _codes["t"] = time.time()
    return out


@app.get("/api/config")
async def config(request: Request):
    u = me(request)
    with db() as c:
        counts = {r["subject"]: r["n"] for r in c.execute(
            "SELECT subject, COUNT(DISTINCT path) n FROM chunks WHERE path LIKE ? GROUP BY subject", (u["vault"] + "/%",))}
    subs = [s for s in subjects() if s["vault"] == u["vault"]]
    codes = course_codes(u["vault"])
    for s in subs:
        s["code"] = s["id"] if re.fullmatch(r"[A-Z]{3,4}M?\d{3,4}", s["id"]) else codes.get(s["id"], "")
        s["ideas"] = latest_ideas(u["vault"], s["id"])
        s["notes"] = sum(n for k, n in counts.items() if f",{s['id']}," in k)
        s["topics"] = []
        for p in sorted(s.pop("_path").glob("topic-*.md")):
            m = re.search(r"^#\s+(.+)$", p.read_text(errors="ignore"), re.M)
            s["topics"].append(re.sub(r"^Topic\s*\d+\s*[-:]\s*", "", m.group(1)) if m else p.stem)
        if not s["topics"]:
            s["topics"] = suggested_topics(u["vault"], s["id"], s["name"])
    ok = allowed_models(u)
    models = [m for m in await model_status() if ok is None or m["id"] in ok]
    return {"subjects": subs, "models": models, "modes": [{"id": k, "label": v[0]} for k, v in MODES.items()]}


@app.get("/api/status")
async def status(request: Request):
    u = me(request)
    with db() as c:
        n = c.execute("SELECT COUNT(*) FROM chunks WHERE path LIKE ?", (u["vault"] + "/%",)).fetchone()[0]
        f = c.execute("SELECT COUNT(*) FROM files WHERE path LIKE ?", (u["vault"] + "/%",)).fetchone()[0]
    ok = allowed_models(u)
    models = [m for m in await model_status() if ok is None or m["id"] in ok]
    vs = await asyncio.to_thread(crunch_info, u["vault"])
    state = "answering" if queue else "reading" if vs["working"] else "idle"
    return {**index_state, "chunks": n, "files": f, "mem_free": mem_available(), "models": models, "state": state,
            "vault": {"working": vs["working"], "pending": vs["pending"], "figures_todo": vs["figures_todo"], "current": vs["current"],
                      "done": vs["done"], "total": vs["total"]}}


@app.post("/api/reindex")
def do_reindex():
    threading.Thread(target=reindex, daemon=True).start()
    return {"ok": True}


@app.get("/api/chats")
def list_chats(request: Request, q: str = ""):
    owner = me(request)["name"]
    with db() as c:
        if q:
            rows = c.execute("SELECT DISTINCT chats.* FROM chats JOIN messages ON messages.chat_id=chats.id "
                             "WHERE chats.owner=? AND (chats.title LIKE ? OR messages.content LIKE ?) "
                             "ORDER BY pinned DESC, updated DESC LIMIT 200", (owner, f"%{q}%", f"%{q}%")).fetchall()
        else:
            rows = c.execute("SELECT * FROM chats WHERE owner=? ORDER BY pinned DESC, updated DESC LIMIT 200", (owner,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/chats/{cid}")
def get_chat(cid: str, request: Request):
    with db() as c:
        chat = c.execute("SELECT * FROM chats WHERE id=? AND owner=?", (cid, me(request)["name"])).fetchone()
        if not chat:
            raise HTTPException(404)
        msgs = c.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY id", (cid,)).fetchall()
    return {**dict(chat), "messages": [{**dict(m), "sources": json.loads(m["sources"] or "[]"),
                                        "meta": json.loads(m["meta"] or "{}")} for m in msgs]}


@app.patch("/api/chats/{cid}")
async def patch_chat(cid: str, req: Request):
    body = await req.json()
    owner = me(req)["name"]
    with db() as c:
        if "title" in body:
            c.execute("UPDATE chats SET title=? WHERE id=? AND owner=?", (body["title"][:120], cid, owner))
        if "pinned" in body:
            c.execute("UPDATE chats SET pinned=? WHERE id=? AND owner=?", (1 if body["pinned"] else 0, cid, owner))
    return {"ok": True}


@app.delete("/api/chats/{cid}")
def delete_chat(cid: str, request: Request):
    with db() as c:
        if c.execute("SELECT 1 FROM chats WHERE id=? AND owner=?", (cid, me(request)["name"])).fetchone():
            c.execute("DELETE FROM messages WHERE chat_id=?", (cid,))
            c.execute("DELETE FROM chats WHERE id=?", (cid,))
    return {"ok": True}


@app.get("/api/chats/{cid}/export")
def export_chat(cid: str, request: Request):
    chat = get_chat(cid, request)
    lines = [f"# {chat['title']}", "", f"*STELLA-80 · {chat['subject']} · {time.strftime('%Y-%m-%d', time.localtime(chat['created']))}*", ""]
    for m in chat["messages"]:
        lines += [f"## {'Q' if m['role'] == 'user' else 'A'}", "", m["content"], ""]
        if m["sources"]:
            lines += ["Sources: " + ", ".join(f"[[{s['path'][:-3]}|{s['title']}]]" for s in m["sources"]), ""]
    fn = re.sub(r"[^\w\- ]", "", chat["title"])[:60] or "chat"
    return HTMLResponse("\n".join(lines), media_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="{fn}.md"'})


def safe_vault_path(rel, user):
    p = vault_resolve(rel)
    if p is None or rel.split("/")[0] != user["vault"] or any(part in SKIP_DIRS for part in p.parts):
        raise HTTPException(404)
    return p


@app.get("/api/note")
def note(path: str, request: Request):
    p = safe_vault_path(path, me(request))
    if p.suffix != ".md" or not p.is_file():
        raise HTTPException(404)
    return {"path": path, "title": p.stem, "text": FRONT.sub("", p.read_text(errors="ignore"))}


@app.get("/vault/{rel:path}")
def vault_file(rel: str, request: Request):
    p = safe_vault_path(rel, me(request))
    if p.suffix.lower() not in IMG_EXT or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------- people: presence, group chat, system panel

def online_list():
    now = time.time()
    asking = {t["user"] for t in queue}
    with db() as c:
        rows = c.execute("SELECT name, display, color, vault, last_seen FROM users WHERE hash IS NOT NULL "
                         "ORDER BY (role='admin') DESC, display").fetchall()
    out = []
    for r in rows:
        pr = presence.get(r["name"], {})
        on = pr.get("t", 0) > now - 45
        out.append({"name": r["name"], "display": r["display"], "color": r["color"], "group": VAULT_TITLE.get(r["vault"], r["vault"]),
                    "online": on, "doing": ("asking Stella" if r["name"] in asking else "away" if pr.get("away") else "online") if on else "offline",
                    "last_seen": max(pr.get("t", 0), r["last_seen"] or 0)})
    return out


@app.post("/api/presence")
async def heartbeat(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    away, active = bool(body.get("away")), bool(body.get("active"))
    u = me(request)
    now = time.time()
    last = presence.get(u["name"], {}).get("t", 0)
    presence[u["name"]] = {"t": now, "away": away}
    if active and not away and 0 < now - last <= 60:   # visible tab and recent interaction: count the gap as study time
        with db() as c:
            c.execute("INSERT INTO study_time(user,day,seconds) VALUES(?,?,?) ON CONFLICT(user,day) DO UPDATE SET seconds=seconds+excluded.seconds",
                      (u["name"], day_of(now), int(min(now - last, 30))))
    if now - last > 120:   # keep "last seen" in the database roughly current without writing on every beat
        with db() as c:
            c.execute("UPDATE users SET last_seen=? WHERE name=?", (now, u["name"]))
    return {"online": online_list()}


@app.get("/api/room")
def room_get(request: Request, since: int = 0):
    u = me(request)
    with db() as c:
        q = ("SELECT room.id, room.user, room.text, room.created, users.display, users.color FROM room "
             "LEFT JOIN users ON users.name=room.user ")
        scope = "" if u["role"] == "admin" else " AND (room.vault IS NULL OR room.vault=?)"
        args = [] if u["role"] == "admin" else [u["vault"]]
        if since:
            rows = c.execute(q + "WHERE room.id>?" + scope + " ORDER BY room.id LIMIT 200", [since, *args]).fetchall()
        else:
            rows = c.execute(q + "WHERE 1=1" + scope + " ORDER BY room.id DESC LIMIT 60", args).fetchall()[::-1]
    return [dict(r) for r in rows]


@app.post("/api/room")
async def room_post(request: Request):
    text = ((await request.json()).get("text") or "").strip()[:1000]
    if not text:
        raise HTTPException(400, "empty")
    with db() as c:
        cur = c.execute("INSERT INTO room(user,text,created) VALUES(?,?,?)", (me(request)["name"], text, time.time()))
    return {"id": cur.lastrowid}


@app.delete("/api/room/{mid}")
def room_delete(mid: int, request: Request):
    u = me(request)
    with db() as c:
        if u["role"] == "admin":
            c.execute("DELETE FROM room WHERE id=?", (mid,))
        else:
            c.execute("DELETE FROM room WHERE id=? AND user=?", (mid, u["name"]))
    return {"ok": True}


def read_cpu():
    try:
        v = list(map(int, open("/proc/stat").readline().split()[1:]))
        return sum(v), v[3] + v[4]   # total, idle (+iowait)
    except OSError:
        return 1, 1


def cpu_temp():
    for h in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            if (h / "name").read_text().strip() in ("k10temp", "coretemp", "zenpower", "cpu_thermal"):
                return int((h / "temp1_input").read_text()) / 1000
        except Exception:
            pass
    return None


SKIP_STATUSES = ("unsupported", "too_big", "no_text", "excluded", "error", "superseded")


def crunch_info(vault):
    """How far the background agent has got with one vault (read-only look at its state database and log)."""
    st = AGENT_STATE.get(vault)
    out = {"vault": vault, "title": VAULT_TITLE.get(vault, vault), "total": 0, "done": 0, "pending": 0, "figures_todo": 0,
           "working": False, "current": None, "updated": None, "skipped": 0}
    if not st or not (st / "state.db").exists():
        return out
    try:
        c = sqlite3.connect(f"file:{st / 'state.db'}?mode=ro", uri=True, timeout=3)
        counts = dict(c.execute("SELECT status, COUNT(*) FROM docs WHERE kind='doc' GROUP BY status").fetchall())
        out["figures_todo"] = c.execute("SELECT COUNT(*) FROM figure_pages WHERE status='todo' AND page > 0").fetchone()[0]
        c.close()
        skipped = sum(counts.get(k, 0) for k in SKIP_STATUSES)   # binary or oversized files: synced, but not readable as text
        out.update(total=sum(counts.values()) - skipped, done=counts.get("done", 0), pending=counts.get("pending", 0), skipped=skipped)
    except Exception:
        pass
    try:
        lg = st / "agent.log"
        age = time.time() - lg.stat().st_mtime
        out["updated"] = lg.stat().st_mtime
        out["working"] = age < 150 and (out["pending"] > 0 or out["figures_todo"] > 0)
        tail = lg.read_bytes()[-6000:].decode(errors="ignore")
        m = re.findall(r"\[(\d+)/(\d+)\] (.+)", tail)
        if out["working"] and m:
            out["current"] = m[-1][2][:80]
    except Exception:
        pass
    return out


@app.get("/api/system")
async def system(request: Request):
    u = me(request)
    t1, i1 = read_cpu()
    await asyncio.sleep(0.4)
    t2, i2 = read_cpu()
    cpu = round(100 * (1 - (i2 - i1) / max(1, t2 - t1)), 1)
    mem = {"MemTotal": 0, "MemAvailable": 0, "SwapTotal": 0, "SwapFree": 0}
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":")
            mem[k] = int(v.split()[0]) * 1024
        up = float(open("/proc/uptime").read().split()[0])
    except OSError:
        up = 0.0
    models = [m["label"] for m in await model_status() if m["loaded"]]
    vaults = [u["vault"]] if u["role"] != "admin" else list(AGENT_STATE)
    crunch = await asyncio.to_thread(lambda: [crunch_info(v) for v in vaults])
    with db() as c:
        notes = c.execute("SELECT COUNT(*) FROM files WHERE path LIKE ?", (u["vault"] + "/%",)).fetchone()[0]
    nq = len(queue)
    busy = next((c for c in crunch if c["working"]), None)
    headline = (f"Stella is answering a question{f' ({nq - 1} waiting)' if nq > 1 else ''}." if nq
                else f"Stella is reading and organising notes ({busy['done']} of {busy['total']} documents done)." if busy
                else "Stella is idle and ready.")
    return {"headline": headline, "cpu": cpu, "load": os.getloadavg()[0], "cores": os.cpu_count(),
            "mem_total": mem["MemTotal"], "mem_avail": mem["MemAvailable"], "swap_used": mem["SwapTotal"] - mem["SwapFree"],
            "temp": cpu_temp(), "uptime_h": up / 3600, "models": models, "queue": nq, "notes": notes, "crunch": crunch,
            "index_running": index_state["running"]}


# ---------------------------------------------------------------- study tracking, board, log, vault info, settings
RECALL_MODES = {"quiz", "cards", "exam", "derive"}   # active recall earns extra points
PALETTE = ["#e0a526", "#4fc3f7", "#c779d0", "#7bc96f", "#ff7a59", "#f06292", "#9575cd", "#26a69a", "#d4e157", "#90a4ae"]
THEMES = {"oled", "amber", "green", "paper"}
SRC_EXT = {".pdf", ".docx", ".pptx", ".md"}


def day_of(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d")


def day_ago(n):
    return day_of(time.time() - n * 86400)


def prefs_of(u):
    try:
        return json.loads(u.get("prefs") or "{}")
    except Exception:
        return {}


def user_days(c, name, since_day="0000-00-00"):
    """Per-day stats for one user, built from study_events (kept even if chats are deleted) and study_time."""
    days = {}

    def d(day):
        return days.setdefault(day, {"day": day, "seconds": 0, "questions": 0, "recall": 0, "subjects": {}, "modes": {}, "topics": {}})
    for r in c.execute("SELECT day, subject, mode, topics FROM study_events WHERE user=? AND day>=?", (name, since_day)):
        x = d(r["day"])
        x["questions"] += 1
        x["modes"][r["mode"]] = x["modes"].get(r["mode"], 0) + 1
        if r["mode"] in RECALL_MODES:
            x["recall"] += 1
        if r["subject"] and r["subject"] != "ALL":
            x["subjects"][r["subject"]] = x["subjects"].get(r["subject"], 0) + 1
        for t in json.loads(r["topics"] or "[]"):
            x["topics"][t] = x["topics"].get(t, 0) + 1
    for r in c.execute("SELECT day, seconds FROM study_time WHERE user=? AND day>=?", (name, since_day)):
        d(r["day"])["seconds"] = r["seconds"]
    return days


def score_of(x):
    return round(x["seconds"] / 60 + 2 * x["questions"] + 3 * x["recall"])


def merge(days):
    out = {"seconds": 0, "questions": 0, "recall": 0, "subjects": {}, "modes": {}, "topics": {}}
    for x in days:
        for k in ("seconds", "questions", "recall"):
            out[k] += x[k]
        for k in ("subjects", "modes", "topics"):
            for a, n in x[k].items():
                out[k][a] = out[k].get(a, 0) + n
    return out


def top(dct, n):
    return [[k, v] for k, v in sorted(dct.items(), key=lambda kv: -kv[1])[:n]]


def streak_of(days):
    s, i = 0, 0
    if not _active(days.get(day_ago(0))):
        i = 1   # an empty today does not break a streak yet
    while _active(days.get(day_ago(i))):
        s += 1
        i += 1
    return s


def _active(x):
    return bool(x) and (x["seconds"] >= 300 or x["questions"] >= 1)


@app.get("/api/board")
def board(request: Request, period: str = "today"):
    viewer = me(request)
    today = day_of(time.time())
    since = {"today": today, "yesterday": day_ago(1), "week": day_ago(6)}.get(period, "0000-00-00")
    until = day_ago(1) if period == "yesterday" else "9999-99-99"
    out = []
    with db() as c:
        urows = c.execute("SELECT name, display, color, vault, prefs FROM users WHERE hash IS NOT NULL").fetchall()
        for u in urows:
            days = user_days(c, u["name"])
            span = merge([x for dd, x in days.items() if since <= dd <= until])
            sharing = prefs_of(dict(u)).get("share", True) or u["name"] == viewer["name"]
            summ = c.execute("SELECT day, summary FROM study_days WHERE user=? AND day>=? AND day<=? ORDER BY day DESC LIMIT 1", (u["name"], since, until)).fetchone()
            pr = presence.get(u["name"], {})
            out.append({"name": u["name"], "display": u["display"], "color": u["color"], "group": VAULT_TITLE.get(u["vault"], u["vault"]),
                        "online": pr.get("t", 0) > time.time() - 45, "score": score_of(span), "seconds": span["seconds"],
                        "questions": span["questions"], "recall": span["recall"], "streak": streak_of(days),
                        "subjects": top(span["subjects"], 3), "modes": span["modes"],
                        "topics": top(span["topics"], 4) if sharing else [], "shared": sharing,
                        "summary": summ["summary"] if summ and sharing else None, "summary_day": summ["day"] if summ else None})
    out.sort(key=lambda r: (-r["score"], -r["seconds"], r["display"]))
    return {"period": period, "rows": out, "you": viewer["name"],
            "formula": "1 point per active minute, 2 per question, 3 more per quiz, cards, exam or derive question"}


@app.get("/api/log")
def study_log(request: Request, user: str = "", days: int = 30):
    viewer = me(request)
    name = user or viewer["name"]
    own = name == viewer["name"]
    with db() as c:
        u = c.execute("SELECT name, display, color, prefs FROM users WHERE name=? AND hash IS NOT NULL", (name,)).fetchone()
        if not u:
            raise HTTPException(404)
        if not own and not prefs_of(dict(u)).get("share", True):
            return {"user": name, "display": u["display"], "private": True, "days": []}
        since = day_ago(max(1, min(days, 120)) - 1)
        per = user_days(c, name, since)
        sums = {r["day"]: r["summary"] for r in c.execute("SELECT day, summary FROM study_days WHERE user=? AND day>=?", (name, since))}
        out = []
        for dd in sorted(per, reverse=True):
            x = per[dd]
            row = {"day": dd, "seconds": x["seconds"], "questions": x["questions"], "recall": x["recall"], "score": score_of(x),
                   "subjects": top(x["subjects"], 4), "modes": x["modes"], "topics": top(x["topics"], 5), "summary": sums.get(dd)}
            if own:
                row["items"] = [{"t": r["ts"], "mode": r["mode"], "subject": r["subject"], "q": r["question"]} for r in c.execute(
                    "SELECT ts, mode, subject, question FROM study_events WHERE user=? AND day=? ORDER BY ts DESC LIMIT 80", (name, dd))]
            out.append(row)
    return {"user": name, "display": u["display"], "own": own, "private": False, "days": out}


@app.put("/api/prefs")
async def put_prefs(request: Request):
    u = me(request)
    body = await request.json()
    prefs = prefs_of(u)
    if body.get("theme") in THEMES:
        prefs["theme"] = body["theme"]
    if body.get("size") in (0.9, 1, 1.15, 1.3):
        prefs["size"] = body["size"]
    if body.get("model") in ENGINE_IDS:
        prefs["model"] = body["model"] if (allowed_models(u) is None or body["model"] in allowed_models(u)) else allowed_models(u)[0]
    if body.get("mode") in MODES:
        prefs["mode"] = body["mode"]
    if body.get("depth") in DEPTH:
        prefs["depth"] = body["depth"]
    if isinstance(body.get("share"), bool):
        prefs["share"] = body["share"]
    if body.get("persona") in PERSONAS:
        prefs["persona"] = body["persona"]
    if isinstance(body.get("custom"), str) and limits_of(u)["custom_persona"]:
        prefs["custom"] = body["custom"].strip()[:600]
    display, color = u["display"], u["color"]
    if isinstance(body.get("display"), str) and 1 <= len(body["display"].strip()) <= 20:
        display = re.sub(r"[<>&\"']", "", body["display"].strip()) or u["display"]
    if isinstance(body.get("color"), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", body["color"]):
        color = body["color"]
    with db() as c:
        c.execute("UPDATE users SET prefs=?, display=?, color=? WHERE name=?", (json.dumps(prefs), display, color, u["name"]))
    return {"ok": True, "prefs": prefs, "display": display, "color": color}


@app.post("/api/password")
async def change_password(request: Request):
    u = me(request)
    body = await request.json()
    cur, new = body.get("current", ""), body.get("new", "")
    if not hmac.compare_digest(hash_pw(cur, u["salt"]), u["hash"]):
        await asyncio.sleep(1.5)
        raise HTTPException(403, "Current passphrase is wrong.")
    if len(new) < 10:
        raise HTTPException(400, "Use at least 10 characters.")
    salt = secrets.token_bytes(16)
    with db() as c:
        c.execute("UPDATE users SET salt=?, hash=? WHERE name=?", (salt.hex(), hash_pw(new, salt.hex()), u["name"]))
    return {"ok": True}


def vault_info(vault):
    """What the background agent has done and is doing for one vault: progress per course, queue, new arrivals."""
    info = crunch_info(vault)
    st, src = AGENT_STATE.get(vault), SRC_ROOTS.get(vault)
    info.update(pending_n=info["pending"], courses=[], pending=[], received=[], processed=[])
    try:
        c = sqlite3.connect(f"file:{st / 'state.db'}?mode=ro", uri=True, timeout=3)
        per = {}
        for course, status, n in c.execute("SELECT json_extract(course,'$[0]'), status, COUNT(*) FROM docs WHERE kind='doc' GROUP BY 1,2"):
            per.setdefault(course, {"course": course, "total": 0, "done": 0})
            if status in SKIP_STATUSES:
                continue
            per[course]["total"] += n
            per[course]["done"] += n if status == "done" else 0
        info["courses"] = sorted(per.values(), key=lambda r: r["course"])
        info["pending"] = [{"title": r[0], "course": r[1]} for r in c.execute(
            "SELECT title, json_extract(course,'$[0]') FROM docs WHERE kind='doc' AND status='pending' ORDER BY id LIMIT 12")]
        state = {r[0]: r[1] for r in c.execute("SELECT path, status FROM docs WHERE kind='doc'")}
        c.close()
        files = []
        for p in src.rglob("*") if src and src.is_dir() else []:
            if p.suffix.lower() in SRC_EXT and p.is_file() and not any(x.startswith(".") for x in p.relative_to(src).parts):
                files.append((p.stat().st_mtime, p))
        for mt, p in sorted(files, reverse=True)[:10]:
            rel = p.relative_to(src)
            info["received"].append({"name": p.name, "course": rel.parts[0] if len(rel.parts) > 1 else "", "t": mt,
                                     "state": {"done": "processed", "pending": "queued"}.get(state.get(str(p)), "waiting" if str(p) not in state else state[str(p)])})
    except Exception:
        pass
    root = VAULTS[vault]["path"] if vault in VAULTS else None
    if root and (root / "_auto/sources").is_dir():
        recent = sorted(((p.stat().st_mtime, p) for p in (root / "_auto/sources").rglob("*.md")), reverse=True)[:8]
        info["processed"] = [{"title": p.stem, "course": p.parent.name, "t": mt} for mt, p in recent]
    return info


@app.get("/api/vault")
async def vault_get(request: Request, vault: str = ""):
    u = me(request)
    v = vault if (u["role"] == "admin" and vault in AGENT_STATE) else u["vault"]
    info = await asyncio.to_thread(vault_info, v)
    with db() as c:
        info["notes"] = c.execute("SELECT COUNT(*) FROM files WHERE path LIKE ?", (v + "/%",)).fetchone()[0]
    info["index_running"] = index_state["running"]
    return info


def auto_summary(evs):
    subj = {}
    for e in evs:
        if e["subject"] and e["subject"] != "ALL":
            subj[e["subject"]] = subj.get(e["subject"], 0) + 1
    modes = {}
    for e in evs:
        modes[e["mode"]] = modes.get(e["mode"], 0) + 1
    s = f"{len(evs)} question{'s' if len(evs) != 1 else ''}"
    if subj:
        s += " on " + ", ".join(k for k, _ in sorted(subj.items(), key=lambda kv: -kv[1])[:3])
    s += ", mostly " + max(modes, key=modes.get) + " mode."
    return s


async def llm_summary(evs):
    """One short plain-English log line from the day's questions, by the local model. None when Stella is busy."""
    if queue or any(log_progress(v)["working"] for v in AGENT_STATE):   # never compete with a vault agent for RAM
        return None
    ticket = {"user": "summary"}
    queue.append(ticket)
    try:
        for _ in range(40):
            if queue[0] is ticket:
                break
            await asyncio.sleep(1.5)
        else:
            return None
        preset, notice = await pick_model("scholar")
        if notice:
            return None
        lines = [f"[{e['subject']}/{e['mode']}] {e['question'][:140]}" for e in evs[:40]]
        msgs = [{"role": "system", "content": "You write one-line study log entries. From a student's questions to a study assistant, write 1 or 2 plain sentences "
                 "(at most 35 words) saying what they worked on and how (for example revising, quizzing themselves). Third person, no name, no quotes, no emoji, no preamble."},
                {"role": "user", "content": "Questions today:\n" + "\n".join(lines)}]
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5)) as client:
            r = await client.post(f"{host_url(preset['host'])}/api/chat", json={
                "model": preset["model"], "messages": msgs, "stream": False, "think": False, "keep_alive": "15m",
                "options": {"num_ctx": 4096, "num_predict": 110, "temperature": 0.3}})
            text = (r.json().get("message", {}).get("content") or "").strip()
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip().strip('"')
        return text[:300] or None
    except Exception:
        return None
    finally:
        if ticket in queue:
            queue.remove(ticket)


async def summarise_pending():
    now = time.time()
    with db() as c:
        users = [r["name"] for r in c.execute("SELECT name FROM users WHERE hash IS NOT NULL")]
    for name in users:
        for day in (day_ago(1), day_ago(0)):
            with db() as c:
                evs = [dict(r) for r in c.execute("SELECT ts, subject, mode, question FROM study_events WHERE user=? AND day=? ORDER BY ts", (name, day))]
                row = c.execute("SELECT sum_q, updated, llm FROM study_days WHERE user=? AND day=?", (name, day)).fetchone()
            if not evs or (row and row["sum_q"] == len(evs) and row["llm"]):
                continue
            if row and day == day_ago(0) and now - row["updated"] < 1500 and row["sum_q"] == len(evs):
                continue
            text = await llm_summary(evs)
            with db() as c:
                c.execute("INSERT INTO study_days(user,day,summary,sum_q,updated,llm) VALUES(?,?,?,?,?,?) ON CONFLICT(user,day) DO UPDATE SET "
                          "summary=excluded.summary, sum_q=excluded.sum_q, updated=excluded.updated, llm=excluded.llm",
                          (name, day, text or auto_summary(evs), len(evs), time.time(), 1 if text else 0))


async def summary_loop():
    await asyncio.sleep(90)
    while True:
        try:
            await summarise_pending()
        except Exception:
            pass
        await asyncio.sleep(1200)


# ---------------------------------------------------------------- limits, tutor personas, admin console, unload
DEFAULT_LIMITS = {"models": ["quick", "scholar"], "think": True, "visuals": True, "images": True, "custom_persona": True,
                  "max_depth": "deep", "daily_q": 0, "locked": False, "upload": True}
DEPTH_ORDER = ["brief", "normal", "deep"]
PERSONAS = {
    "default": ("Default", ""),
    "direct": ("Short and straight to the point", "Be extremely concise. Lead with the answer, no preamble, no pleasantries, no recap. Use short bullets or short sentences. Skip anything the student did not ask for."),
    "strict": ("Strict teacher", "Act as a strict, demanding teacher. Be formal and exacting about definitions and notation, point out sloppy reasoning directly, and end with one short check question. No flattery."),
    "friendly": ("Friendly and caring", "Be warm, patient and encouraging, like a kind older student. Acknowledge effort, normalise confusion and explain gently. Stay accurate."),
    "socratic": ("Socratic coach", "Guide with questions. Before the full answer, ask one or two short leading questions, then explain. Keep the student thinking."),
    "examcoach": ("Exam coach", "Focus on what earns marks: what examiners expect, common mistakes, time-saving tricks and how to structure answers. Be brisk and practical."),
    "simple": ("Plain and simple", "Use everyday words and analogies first, jargon only after. Short paragraphs. Finish with one easy question to check understanding."),
    "obedient": ("Obedient assistant", "Do exactly what is asked, no more and no less. Do not lecture, add warnings or push back on the request. Follow formatting instructions literally."),
}


def limits_of(u):
    lim = dict(DEFAULT_LIMITS)
    try:
        lim.update(json.loads(u.get("limits") or "{}"))
    except Exception:
        pass
    if u["role"] == "admin":
        lim.update(models=list(ENGINE_IDS), think=True, visuals=True, images=True, custom_persona=True,
                   max_depth="deep", daily_q=0, locked=False)
    return lim


def allowed_models(u):
    return None if u["role"] == "admin" else tuple(limits_of(u)["models"])


def persona_text(u):
    p = prefs_of(u)
    lim = limits_of(u)
    parts = [PERSONAS.get(p.get("persona", "default"), PERSONAS["default"])[1]]
    if lim["custom_persona"] and (p.get("custom") or "").strip():
        parts.append("The student also asks: " + p["custom"].strip()[:600])
    return " ".join(x for x in parts if x)


PASS_WORDS = ("amber azure basil birch cedar coral dune ember fern flint frost glade harbor iris jade larch lilac maple moss oak "
              "olive opal pine quartz reed sage slate sorrel tide umber willow").split()


def new_passphrase():
    return "-".join(secrets.choice(PASS_WORDS) for _ in range(3)) + "-" + str(secrets.randbelow(90) + 10)


def set_passphrase(c, name, pw):
    salt = secrets.token_bytes(16).hex()
    return c.execute("UPDATE users SET salt=?, hash=? WHERE name=?", (salt, hash_pw(pw, salt), name)).rowcount


def add_user(c, name, display="", vault="", admin=False):
    """Create an account with a random passphrase and return it (shown once). Raises ValueError with a readable reason."""
    name = re.sub(r"[^a-z0-9_-]", "", (name or "").lower())[:20]
    if not name or name == "stella":
        raise ValueError("Pick another name: lower-case letters, digits, - and _.")
    if c.execute("SELECT 1 FROM users WHERE name=?", (name,)).fetchone():
        raise ValueError(f"{name} already exists.")
    vault = vault or DEFAULT_VAULT
    if vault not in VAULTS:
        raise ValueError(f"No vault called {vault}.")
    n = c.execute("SELECT COUNT(*) FROM users WHERE role!='system'").fetchone()[0]
    c.execute("INSERT INTO users(name,display,vault,role,color,profile) VALUES(?,?,?,?,?,?)",
              (name, ((display or "").strip() or name.capitalize())[:20], vault, "admin" if admin else "student",
               PALETTE[n % len(PALETTE)], vault))
    pw = new_passphrase()
    set_passphrase(c, name, pw)
    return name, pw


def remove_user(c, name):
    """Delete an account with its chats, study history and group-chat messages."""
    for (cid,) in c.execute("SELECT id FROM chats WHERE owner=?", (name,)).fetchall():
        c.execute("DELETE FROM messages WHERE chat_id=?", (cid,))
    for sql in ("DELETE FROM chats WHERE owner=?", "DELETE FROM study_events WHERE user=?", "DELETE FROM study_time WHERE user=?",
                "DELETE FROM study_days WHERE user=?", "DELETE FROM room WHERE user=?"):
        c.execute(sql, (name,))
    presence.pop(name, None)
    return c.execute("DELETE FROM users WHERE name=? AND role!='system'", (name,)).rowcount


def need_admin(request):
    u = me(request)
    if u["role"] != "admin":
        raise HTTPException(403, "Admin only.")
    return u


@app.get("/api/admin/users")
def admin_users(request: Request):
    need_admin(request)
    today = day_of(time.time())
    out = []
    with db() as c:
        for r in c.execute("SELECT * FROM users WHERE role!='system' ORDER BY (role='admin') DESC, display").fetchall():
            u = dict(r)
            q_today = c.execute("SELECT COUNT(*) FROM study_events WHERE user=? AND day=?", (u["name"], today)).fetchone()[0]
            q_all = c.execute("SELECT COUNT(*) FROM study_events WHERE user=?", (u["name"],)).fetchone()[0]
            secs = c.execute("SELECT seconds FROM study_time WHERE user=? AND day=?", (u["name"], today)).fetchone()
            pr = presence.get(u["name"], {})
            out.append({"name": u["name"], "display": u["display"], "color": u["color"], "vault": VAULT_TITLE.get(u["vault"], u["vault"]),
                        "role": u["role"], "has_pass": bool(u["hash"]), "limits": limits_of(u), "online": pr.get("t", 0) > time.time() - 45,
                        "last_seen": max(pr.get("t", 0), u["last_seen"] or 0), "q_today": q_today, "q_all": q_all, "secs_today": secs[0] if secs else 0})
    return {"users": out, "personas": {k: v[0] for k, v in PERSONAS.items()}, "mode": MODE,
            "vaults": [{"id": k, "title": v} for k, v in VAULT_TITLE.items()]}


@app.post("/api/admin/users")
async def admin_add_user(request: Request):
    need_admin(request)
    if SOLO:
        raise HTTPException(400, "Solo mode has no accounts. Set mode: shared in stella80.yaml to add people.")
    body = await request.json()
    try:
        with db() as c:
            name, pw = add_user(c, body.get("name"), body.get("display"), body.get("vault"), body.get("admin") is True)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"name": name, "passphrase": pw}


@app.delete("/api/admin/users/{name}")
def admin_remove_user(name: str, request: Request):
    admin = need_admin(request)
    if name == admin["name"]:
        raise HTTPException(400, "You cannot remove your own account.")
    with db() as c:
        if not remove_user(c, name):
            raise HTTPException(404)
    return {"ok": True}


@app.put("/api/admin/users/{name}")
async def admin_set_user(name: str, request: Request):
    admin = need_admin(request)
    body = await request.json()
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
        if not r:
            raise HTTPException(404)
        cur = {}
        try:
            cur = json.loads(r["limits"] or "{}")
        except Exception:
            pass
        if name == admin["name"] and body.get("locked"):
            raise HTTPException(400, "You cannot lock yourself out.")
        for k in ("think", "visuals", "images", "custom_persona", "locked", "upload"):
            if isinstance(body.get(k), bool):
                cur[k] = body[k]
        if isinstance(body.get("models"), list):
            cur["models"] = [m for m in body["models"] if m in ENGINE_IDS] or ["quick"]
        if body.get("max_depth") in DEPTH_ORDER:
            cur["max_depth"] = body["max_depth"]
        if isinstance(body.get("daily_q"), int) and 0 <= body["daily_q"] <= 10000:
            cur["daily_q"] = body["daily_q"]
        c.execute("UPDATE users SET limits=? WHERE name=?", (json.dumps(cur), name))
    return {"ok": True}


@app.post("/api/admin/users/{name}/passphrase")
def admin_new_passphrase(name: str, request: Request):
    need_admin(request)
    pw = new_passphrase()
    with db() as c:
        if not set_passphrase(c, name, pw):
            raise HTTPException(404)
    return {"passphrase": pw}


@app.delete("/api/chats")
def delete_all_chats(request: Request):
    owner = me(request)["name"]
    with db() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM chats WHERE owner=?", (owner,))]
        for i in ids:
            c.execute("DELETE FROM messages WHERE chat_id=?", (i,))
        c.execute("DELETE FROM chats WHERE owner=?", (owner,))
    return {"deleted": len(ids)}


@app.post("/api/unload")
async def unload_models(request: Request):
    """Free memory by unloading models. Anyone may unload the study instance when nobody is waiting;
    the background instance (also used by the vault agents) is admin only and only when no agent is mid-run."""
    u = me(request)
    if queue:
        raise HTTPException(409, "Stella is answering a question right now.")
    freed, kept_busy = [], False
    async with httpx.AsyncClient() as client:
        ps = await ollama_get(client, STUDY_OLLAMA, "/api/ps") or {}
        for m in ps.get("models", []):
            if STUDY_OLLAMA == BG_OLLAMA and (m["name"] == EMBED_MODEL or u["role"] != "admin"):
                continue   # one shared instance: what is left is handled below
            await client.post(f"{STUDY_OLLAMA}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=30)
            freed.append(m["name"])
        if u["role"] == "admin":
            busy = False
            for v in AGENT_STATE:
                if (await asyncio.to_thread(crunch_info, v))["working"]:
                    busy = True
            cps = await ollama_get(client, BG_OLLAMA, "/api/ps") or {} if BG_OLLAMA != STUDY_OLLAMA else {"models": [x for x in ps.get("models", []) if x["name"] not in freed]}
            for m in cps.get("models", []):
                if m["name"] == EMBED_MODEL:
                    continue
                if busy:
                    kept_busy = True
                    continue
                await client.post(f"{BG_OLLAMA}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=30)
                freed.append(m["name"])
            cpu_used.clear()
    await asyncio.sleep(1.5)
    return {"unloaded": freed, "mem_free": mem_available(), "kept_busy": kept_busy}


def lim_daily_exceeded(u):
    cap = limits_of(u)["daily_q"]
    if not cap:
        return False
    with db() as c:
        n = c.execute("SELECT COUNT(*) FROM study_events WHERE user=? AND day=?", (u["name"], day_of(time.time()))).fetchone()[0]
    return n >= cap


# ---------------------------------------------------------------- processing queue (what the vault agents will read next)
def agent_db(vault, write=False):
    st = AGENT_STATE.get(vault)
    if not st or not (st / "state.db").exists():
        raise HTTPException(404, "No agent database for that vault yet.")
    c = sqlite3.connect(st / "state.db", timeout=30)
    c.row_factory = sqlite3.Row
    if "priority" not in [r[1] for r in c.execute("PRAGMA table_info(docs)")]:
        c.execute("ALTER TABLE docs ADD COLUMN priority INTEGER DEFAULT 0")
        c.commit()
    return c


def queue_vault(request, vault):
    u = me(request)
    v = vault or u["vault"]
    if u["role"] != "admin" and v != u["vault"]:
        raise HTTPException(403, "Not your vault.")
    if v not in AGENT_STATE:
        raise HTTPException(404, "This vault is read as it is; it has no vault agent and so no processing queue.")
    return u, v


def log_progress(vault):
    """What the agent is doing now, from its log, only while it is actually working: a document, or the slide-image pass."""
    out = {"working": False, "phase": None, "name": None, "n": None, "of": None, "chunk": None, "chunks": None, "page": None}
    try:
        lg = AGENT_STATE[vault] / "agent.log"
        if time.time() - lg.stat().st_mtime > 150:
            return out
        tail = lg.read_bytes()[-12000:].decode(errors="ignore")
        docs = list(re.finditer(r"\[(\d+)/(\d+)\] (.+)", tail))
        vis = list(re.finditer(r"\n[\d:]+\s+(.+?) p\.(\d+): ", tail))
        last_doc = docs[-1].start() if docs else -1
        if vis and vis[-1].start() > last_doc:
            out.update(working=True, phase="vision", name=vis[-1].group(1).strip(), page=int(vis[-1].group(2)))
            return out
        if not docs:
            return out
        d = docs[-1]
        ch = re.findall(r"chunk (\d+)/(\d+)", tail[d.end():])
        out.update(working=True, phase="text", n=int(d.group(1)), of=int(d.group(2)), name=d.group(3).strip())
        if ch:
            out.update(chunk=int(ch[-1][0]), chunks=int(ch[-1][1]))
    except Exception:
        pass
    return out


def row_info(r, src):
    p = Path(r["path"])
    try:
        size = p.stat().st_size / 1e6
    except Exception:
        size = None
    return {"id": r["id"], "title": r["title"], "course": json.loads(r["course"])[0], "type": r["doctype"], "size_mb": size,
            "priority": r["priority"] or 0, "file": p.name}


@app.get("/api/queue")
async def queue_get(request: Request, vault: str = ""):
    u, v = queue_vault(request, vault)

    def work():
        c = agent_db(v)
        pend = [row_info(r, None) for r in c.execute(
            "SELECT id,path,title,course,doctype,priority FROM docs WHERE kind='doc' AND status='pending' "
            "ORDER BY priority DESC, id LIMIT 200")]
        excl = [row_info(r, None) for r in c.execute(
            "SELECT id,path,title,course,doctype,priority FROM docs WHERE kind='doc' AND status='excluded' ORDER BY title LIMIT 200")]
        counts = dict(c.execute("SELECT status, COUNT(*) FROM docs WHERE kind='doc' GROUP BY status").fetchall())
        c.close()
        return pend, excl, counts
    pend, excl, counts = await asyncio.to_thread(work)
    prog = await asyncio.to_thread(log_progress, v)
    for i, r in enumerate(pend, 1):
        r["position"] = i
        r["current"] = bool(prog["working"] and prog["name"] and r["file"] == prog["name"])
    return {"vault": v, "title": VAULT_TITLE.get(v, v), "progress": prog, "next": pend, "excluded": excl, "counts": counts,
            "can_edit": u["role"] == "admin",
            "vaults": [{"id": k, "title": VAULT_TITLE.get(k, k)} for k in AGENT_STATE] if u["role"] == "admin" else []}


@app.get("/api/queue/docs")
async def queue_docs(request: Request, vault: str = "", q: str = "", status: str = ""):
    """Every file the agent knows about in a vault, filterable, for reprocessing or removal."""
    u, v = queue_vault(request, vault)

    def work():
        c = agent_db(v)
        sql = "SELECT id,path,title,course,doctype,priority,status FROM docs WHERE kind='doc'"
        args = []
        if q:
            sql += " AND (title LIKE ? OR path LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
        if status:
            sql += " AND status=?"
            args.append(status)
        rows = [{**row_info(r, None), "status": r["status"]} for r in c.execute(sql + " ORDER BY json_extract(course,'$[0]'), title LIMIT 300", args)]
        total = c.execute("SELECT COUNT(*) FROM docs WHERE kind='doc'").fetchone()[0]
        c.close()
        return rows, total
    rows, total = await asyncio.to_thread(work)
    return {"vault": v, "docs": rows, "total": total, "can_edit": u["role"] == "admin"}


@app.post("/api/queue/action")
async def queue_action(request: Request):
    need_admin(request)
    body = await request.json()
    v = body.get("vault")
    act = body.get("action")
    ids = [int(i) for i in (body.get("ids") or []) if str(i).lstrip("-").isdigit()][:300]
    if v not in AGENT_STATE or act not in ("top", "bottom", "normal", "exclude", "include", "reprocess") or not ids:
        raise HTTPException(400, "Bad request.")

    def work():
        c = agent_db(v, write=True)
        q = ",".join("?" * len(ids))
        if act in ("top", "bottom"):
            hi = c.execute("SELECT COALESCE(MAX(priority),0) FROM docs WHERE status='pending'").fetchone()[0]
            lo = c.execute("SELECT COALESCE(MIN(priority),0) FROM docs WHERE status='pending'").fetchone()[0]
            val = hi + 1 if act == "top" else lo - 1
            c.execute(f"UPDATE docs SET priority=? WHERE status='pending' AND id IN ({q})", [val, *ids])
        elif act == "normal":
            c.execute(f"UPDATE docs SET priority=0 WHERE status='pending' AND id IN ({q})", ids)
        elif act == "exclude":   # not read by the model and left out of the vault; the file itself stays untouched
            for t in ("chunks", "mentions"):   # a processed file also loses its contribution to the graph at the next render
                c.execute(f"DELETE FROM {t} WHERE doc_id IN (SELECT id FROM docs WHERE status='done' AND id IN ({q}))", ids)
            c.execute(f"UPDATE docs SET status='excluded', priority=0 WHERE status IN ('pending','done','no_text','unsupported','too_big','error') AND id IN ({q})", ids)
        elif act == "reprocess":   # run the model over a finished file again, first in line
            hi = c.execute("SELECT COALESCE(MAX(priority),0) FROM docs WHERE status='pending'").fetchone()[0]
            for t in ("chunks", "mentions"):
                c.execute(f"DELETE FROM {t} WHERE doc_id IN (SELECT id FROM docs WHERE status IN ('done','no_text','unsupported','too_big','error') AND id IN ({q}))", ids)
            c.execute(f"UPDATE docs SET status='pending', priority=? WHERE status IN ('done','no_text','unsupported','too_big','error') AND id IN ({q})", [hi + 1, *ids])
        else:
            c.execute(f"UPDATE docs SET status='pending', priority=0 WHERE status='excluded' AND id IN ({q})", ids)
        n = c.total_changes
        c.commit()
        c.close()
        return n
    return {"ok": True, "changed": await asyncio.to_thread(work)}


# ---------------------------------------------------------------- Discord bot API (token gated, same functions as the web UI)
@app.get("/api/bot/board")
def bot_board(request: Request, period: str = "today"):
    return board(request, period)


@app.get("/api/bot/online")
def bot_online():
    return {"online": online_list()}


@app.get("/api/bot/users")
def bot_users(request: Request):
    return admin_users(request)


@app.put("/api/bot/users/{name}")
async def bot_set_user(name: str, request: Request):
    return await admin_set_user(name, request)


@app.get("/api/bot/queue")
async def bot_queue(request: Request, vault: str = ""):
    return await queue_get(request, vault)


@app.post("/api/bot/queue/action")
async def bot_queue_action(request: Request):
    return await queue_action(request)


@app.get("/api/bot/system")
async def bot_system(request: Request):
    return await system(request)


@app.get("/api/bot/vault")
async def bot_vault(request: Request, vault: str = ""):
    return await vault_get(request, vault)


# ---------------------------------------------------------------- uploads into a vault, and Stella's group-chat notices about problem files
UP_DENY = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg", ".mp3", ".wav", ".m4a", ".flac", ".aac",
           ".ogg", ".wma", ".opus", ".exe", ".dll", ".so", ".dylib", ".pyc", ".class", ".o", ".iso", ".dmg", ".msi", ".tmp", ".part", ".crdownload"}
UP_MAX = 60 * 1024 * 1024        # largest upload accepted
AGENT_CAP_MB = 25                # largest file the agent reads as text (see max_file_mb in its config)


def agent_courses(vault):
    """{source folder: course tuple} for a vault, exactly as its agent sees them, and whether new subjects may be created."""
    if vault not in AGENT_CFG:   # a plain Obsidian vault without an agent has nothing to upload into
        return {}, False
    cfg = yaml.safe_load(AGENT_CFG[vault].read_text())
    src = SRC_ROOTS[vault]
    if cfg.get("auto_courses"):
        out = {}
        for d in sorted(src.iterdir()):
            if d.is_dir() and not d.name.startswith((".", "_")) and d.name not in (cfg.get("exclude_src") or []):
                slug = re.sub(r"[^A-Za-z0-9]+", "-", d.name).strip("-")
                if slug:
                    out[d.name] = [slug, "00-" + slug, slug.lower()]
        return out, True
    return {k: list(v) for k, v in (cfg.get("courses") or {}).items()}, False


def upload_vault(u, vault):
    return vault if (u["role"] == "admin" and vault in SRC_ROOTS) else u["vault"]


@app.get("/api/upload/targets")
def upload_targets(request: Request, vault: str = ""):
    u = me(request)
    v = upload_vault(u, vault)
    courses, can_new = agent_courses(v)
    return {"vault": v, "title": VAULT_TITLE.get(v, v), "can_new": can_new, "max_mb": UP_MAX // 2**20, "read_mb": AGENT_CAP_MB,
            "subjects": [{"folder": k, "label": t[0], "name": k, "code": course_codes(v).get(t[0], "") or (t[0].split("-")[0] if re.match(r"[A-Z]{3,4}M?\d{3,4}-", t[0]) else "")} for k, t in courses.items()],
            "vaults": [{"id": k, "title": VAULT_TITLE.get(k, k)} for k in SRC_ROOTS] if u["role"] == "admin" else [],
            "allowed": bool(limits_of(u).get("upload", True)) and v in SRC_ROOTS,
            "why": "" if v in SRC_ROOTS else "This vault is read as it is (no vault agent), so there is nothing to upload into."}


def post_stella(text, vault=None):
    with db() as c:
        c.execute("INSERT INTO room(user,text,created,vault) VALUES('stella',?,?,?)", (text, time.time(), vault))


@app.put("/api/upload")
async def upload_file(request: Request, vault: str = "", folder: str = "", name: str = "", new: int = 0, title: str = "", force: int = 0):
    u = me(request)
    if not limits_of(u).get("upload", True):
        raise HTTPException(403, "Uploads are turned off for your account.")
    v = upload_vault(u, vault)
    if v not in SRC_ROOTS:
        raise HTTPException(400, "This vault has no vault agent, so there is nowhere to upload to.")
    courses, can_new = agent_courses(v)
    folder = folder.strip()
    if folder in courses:
        tup = courses[folder]
    elif new and can_new:
        folder = re.sub(r"[^\w .&()+-]", "", folder).strip()[:60]
        slug = re.sub(r"[^A-Za-z0-9]+", "-", folder).strip("-")
        if len(folder) < 2 or not slug:
            raise HTTPException(400, "Give the new subject a name.")
        tup = [slug, "00-" + slug, slug.lower()]
    else:
        raise HTTPException(400, "Choose a subject.")
    fname = re.sub(r"[\x00-\x1f/\\]", "", Path(name).name).strip()[:120]
    ext = Path(fname).suffix.lower()
    if not fname or not ext or ext in UP_DENY or fname.startswith("."):
        raise HTTPException(400, "That file type is not accepted (video, audio and programs are not processed).")
    dest_dir = SRC_ROOTS[v] / folder / "_uploads"   # keep '_uploads' out of any sync so uploads stay on the server
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / fname
    tmp = dest_dir / ("." + fname + ".part")
    size, h = 0, hashlib.sha256()
    try:
        with tmp.open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > UP_MAX:
                    raise HTTPException(413, f"Larger than {UP_MAX // 2**20} MB.")
                h.update(chunk)
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, "Empty file.")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    sha = h.hexdigest()

    def register():
        c = agent_db(v, write=True)
        dup = c.execute("SELECT id, title, status FROM docs WHERE sha=? AND path!=? AND kind='doc' LIMIT 1", (sha, str(dest))).fetchone()
        if dup and force and dup["status"] in ("error", "too_big", "no_text", "done", "superseded", "excluded"):   # read the copy already stored once more
            hi = c.execute("SELECT COALESCE(MAX(priority),0) FROM docs WHERE status='pending'").fetchone()[0] + 1
            for t in ("chunks", "mentions"):
                c.execute(f"DELETE FROM {t} WHERE doc_id=?", (dup["id"],))
            c.execute("UPDATE docs SET status='pending', priority=? WHERE id=?", (hi, dup["id"]))
            c.commit()
            c.close()
            return {"requeued": True}
        if dup:
            return {"duplicate": dup["title"], "status": dup["status"]}
        os.replace(tmp, dest)
        hi = c.execute("SELECT COALESCE(MAX(priority),0) FROM docs WHERE status='pending'").fetchone()[0] + 1
        row = c.execute("SELECT id FROM docs WHERE path=?", (str(dest),)).fetchone()
        if row:   # same name again: treat as a replacement, read it afresh
            for t in ("chunks", "mentions"):
                c.execute(f"DELETE FROM {t} WHERE doc_id=?", (row["id"],))
            c.execute("UPDATE docs SET sha=?, status='pending', priority=?, course=? WHERE id=?", (sha, hi, json.dumps(tup), row["id"]))
        else:
            c.execute("INSERT INTO docs(path,sha,kind,course,title,status,doctype,link,priority) VALUES(?,?,'doc',?,?,'pending',?,'',?)",
                      (str(dest), sha, json.dumps(tup), (title.strip()[:120] or Path(fname).stem.replace("_", " ")), ext.lstrip("."), hi))
        c.commit()
        c.close()
        return {"queued": True}
    res = await asyncio.to_thread(register)
    if res.get("requeued"):
        tmp.unlink(missing_ok=True)
        post_stella(f'{u["display"]} asked for "{fname}" to be read again. It is first in the processing queue.', v)
        return {"ok": True, "file": fname, "subject": tup[0], "size": size, "requeued": True}
    if "duplicate" in res:
        tmp.unlink(missing_ok=True)
        why = "it is already in the vault" + (" but could not be read, so a different copy is needed" if res["status"] in ("error", "too_big") else "")
        raise HTTPException(409, f'"{fname}": {why} (as "{res["duplicate"]}").')
    big = size > AGENT_CAP_MB * 2**20
    post_stella(f'{u["display"]} uploaded "{fname}" to {tup[0]}. It is first in the processing queue.' + (f" It is over {AGENT_CAP_MB} MB, so it may be skipped." if big else ""), v)
    return {"ok": True, "file": fname, "subject": tup[0], "size": size, "over_cap": big}


def doc_watch_once():
    """Tell the group about files that could not be processed, and when they are fixed (re-synced or re-uploaded)."""
    for vault in AGENT_STATE:
        try:
            c = agent_db(vault)
        except HTTPException:
            continue
        rows = [dict(r) for r in c.execute("SELECT id,path,title,course,status,sha FROM docs WHERE kind='doc'")]
        c.close()
        fixed_ids = []
        with db() as m:
            for r in rows:
                file, course = Path(r["path"]).name, json.loads(r["course"])[0]
                al = m.execute("SELECT * FROM doc_alerts WHERE vault=? AND path=?", (vault, r["path"])).fetchone()
                if r["status"] in ("error", "too_big") and (not al or al["sha"] != r["sha"]):
                    msg = (f'Problem: "{file}" ({course}) could not be read. It is most likely corrupt or password protected. Upload a clean copy with the same name '
                           f"(Vault tab, UPLOAD FILES) or sync a fixed one, and I will say when it is sorted." if r["status"] == "error" else
                           f'"{file}" ({course}) is larger than {AGENT_CAP_MB} MB, so it was not processed. A smaller or split copy will be.')
                    m.execute("INSERT OR REPLACE INTO doc_alerts(vault,path,sha,kind,announced,resolved) VALUES(?,?,?,?,?,NULL)", (vault, r["path"], r["sha"], r["status"], time.time()))
                    m.execute("INSERT INTO room(user,text,created,vault) VALUES('stella',?,?,?)", (msg, time.time(), vault))
                elif al and not al["resolved"] and r["status"] == "done":
                    m.execute("UPDATE doc_alerts SET resolved=? WHERE vault=? AND path=?", (time.time(), vault, r["path"]))
                    m.execute("INSERT INTO room(user,text,created,vault) VALUES('stella',?,?,?)", (f'Fixed: "{file}" ({course}) is now processed.', time.time(), vault))
            # a problem file replaced by an upload of the same name: the old entry is superseded and the problem is solved
            for al in m.execute("SELECT * FROM doc_alerts WHERE vault=? AND resolved IS NULL", (vault,)).fetchall():
                name = Path(al["path"]).name.lower()
                twin = next((r for r in rows if r["status"] == "done" and Path(r["path"]).name.lower() == name and r["path"] != al["path"]), None)
                if twin:
                    old = next((r for r in rows if r["path"] == al["path"]), None)
                    m.execute("UPDATE doc_alerts SET resolved=? WHERE vault=? AND path=?", (time.time(), vault, al["path"]))
                    m.execute("INSERT INTO room(user,text,created,vault) VALUES('stella',?,?,?)",
                              (f'Fixed: "{Path(twin["path"]).name}" ({json.loads(twin["course"])[0]}) was uploaded again and is now processed.', time.time(), vault))
                    if old:
                        fixed_ids.append(old["id"])
        if fixed_ids:
            w = agent_db(vault, write=True)
            w.execute(f"UPDATE docs SET status='superseded' WHERE id IN ({','.join('?' * len(fixed_ids))})", fixed_ids)
            w.commit()
            w.close()


async def doc_watch_loop():
    await asyncio.sleep(45)
    while True:
        try:
            await asyncio.to_thread(doc_watch_once)
        except Exception:
            pass
        await asyncio.sleep(60)


@app.post("/api/chat")
async def chat(req: Request):
    body = await req.json()
    user = me(req)
    vault = user["vault"]
    question = (body.get("message") or "").strip()
    if not question:
        raise HTTPException(400, "empty message")
    if lim_daily_exceeded(user):
        raise HTTPException(429, f"You have used today's {limits_of(user)['daily_q']} questions. Ask {ADMIN_WORD} if you need more.")
    subject = body.get("subject") or "ALL"
    mode = body.get("mode") if body.get("mode") in MODES else "teach"
    depth = body.get("depth") if body.get("depth") in DEPTH else "normal"
    grounding = body.get("grounding") or "blend"
    think = bool(body.get("think"))
    visuals = body.get("visuals", True)
    images = [i.split(",", 1)[-1] for i in (body.get("images") or [])][:3]
    lim = limits_of(user)
    think = think and lim["think"]
    visuals = bool(visuals) and lim["visuals"]
    images = images if lim["images"] else []
    if DEPTH_ORDER.index(depth) > DEPTH_ORDER.index(lim["max_depth"]):
        depth = lim["max_depth"]
    persona = persona_text(user)
    k, ctx_chars, num_predict = DEPTH[depth]
    if think:
        num_predict += 2500

    cid = body.get("chat_id")
    now = time.time()
    with db() as c:
        if not cid or not c.execute("SELECT 1 FROM chats WHERE id=? AND owner=?", (cid, user["name"])).fetchone():
            cid = uuid.uuid4().hex[:12]
            c.execute("INSERT INTO chats(id,title,subject,created,updated,pinned,owner) VALUES(?,?,?,?,?,0,?)",
                      (cid, question[:70], subject, now, now, user["name"]))
        history = c.execute("SELECT role, content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 6", (cid,)).fetchall()[::-1]
        c.execute("INSERT INTO messages(chat_id,role,content,meta,created) VALUES(?,?,?,?,?)",
                  (cid, "user", question, json.dumps({"mode": mode, "subject": subject, "images": len(images)}), now))

    subs = {s["id"]: s for s in subjects() if s["vault"] == vault}
    subject_name = f"{subject} {subs[subject]['name']}" if subject in subs else ""
    # Follow-ups like "and why?" retrieve poorly alone; lean on the previous question.
    prev_q = next((h["content"] for h in reversed(history) if h["role"] == "user"), "")
    rq = question if len(question) > 60 or not prev_q else f"{prev_q}\n{question}"

    async def stream():
        ticket = {"user": user["name"]}
        try:
            async for chunk in stream_inner(ticket):
                yield chunk
        finally:
            if ticket in queue:
                queue.remove(ticket)

    async def stream_inner(ticket):
        def ev(**kw):
            return json.dumps(kw) + "\n"
        yield ev(type="chat", chat_id=cid)
        t0 = time.time()
        try:
            hits = await asyncio.to_thread(retrieve, rq, subject, k, vault)
        except Exception as e:
            hits = []
            yield ev(type="notice", text=f"Vault search failed ({e}); answering without notes.")
        ctx, used = [], 0
        for i, h in enumerate(hits, 1):
            block = f"[{i}] {h['title']}" + (f" > {h['heading']}" if h["heading"] else "") + f" ({h['kind']} note)\n{h['text']}"
            if used + len(block) > ctx_chars and ctx:
                hits = hits[:i - 1]
                break
            ctx.append(block); used += len(block)
        sources = [{"n": i, "path": h["path"], "title": h["title"], "heading": h["heading"], "kind": h["kind"],
                    "subject": h["subject"].strip(","), "snippet": h["text"][:280], "images": h["images"][:4]}
                   for i, h in enumerate(hits, 1)]
        yield ev(type="sources", sources=sources, ms=int((time.time() - t0) * 1000))
        topics = list(dict.fromkeys(h["title"] for h in hits if h["kind"] in ("human", "processed")))[:4]
        with db() as c:
            c.execute("INSERT INTO study_events(user,ts,day,subject,mode,question,topics) VALUES(?,?,?,?,?,?,?)",
                      (user["name"], now, day_of(now), subject, mode, question[:300], json.dumps(topics)))

        queue.append(ticket)   # one answer at a time on the model; everyone else waits in line
        shown = None
        while queue[0] is not ticket:
            ahead = queue.index(ticket)
            if ahead != shown:
                shown = ahead
                yield ev(type="queue", ahead=ahead)
            await asyncio.sleep(1.5)
        ok = allowed_models(user)
        want = body.get("model") or "scholar"
        if ok is not None and want not in ok:
            want = ok[0]
        preset, notice = await pick_model(want)
        if notice:
            yield ev(type="notice", text=notice)
        if images and not preset.get("vision"):
            yield ev(type="notice", text=f"{preset['label']} can't see images; switching to SCHOLAR for this one.")
            preset, _ = await pick_model("scholar")
        yield ev(type="model", label=preset["label"], model=preset["model"])

        msgs = [{"role": "system", "content": system_prompt(subject_name, mode, grounding, visuals, PROFILE.get(vault, {"who": "a university student", "code": CODE_HINT}), persona)}]
        hist_chars = 0
        for h in history[::-1]:
            hist_chars += len(h["content"])
            if hist_chars > 5000:
                break
            msgs.insert(1, {"role": h["role"], "content": h["content"][:2500]})
        user_msg = ("VAULT EXCERPTS:\n\n" + "\n\n---\n\n".join(ctx) if ctx else "VAULT EXCERPTS: (nothing relevant found)") + \
               f"\n\nQUESTION: {question}"
        if visuals and DRAW_RE.search(question):
            user_msg += DRAW_NOTE
        um = {"role": "user", "content": user_msg}
        if images:
            um["images"] = images
        msgs.append(um)

        payload = {"model": preset["model"], "messages": msgs, "stream": True, "think": think,
                   "keep_alive": "15m" if preset["host"] == "study" else "5m", "options": {"num_ctx": NUM_CTX + (4096 if think else 0), "num_predict": num_predict,
                                                    "temperature": 0.4 if mode != "quiz" else 0.7}}
        if preset["host"] == "background":
            cpu_used[preset["model"]] = time.time()
        answer, thinking, stats = "", "", {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as client:
                async with client.stream("POST", f"{host_url(preset['host'])}/api/chat", json=payload) as r:
                    if r.status_code != 200:
                        raise RuntimeError((await r.aread()).decode()[:300])
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        d = json.loads(line)
                        if d.get("error"):
                            raise RuntimeError(d["error"])
                        m = d.get("message", {})
                        if m.get("thinking"):
                            thinking += m["thinking"]
                            yield ev(type="think", text=m["thinking"])
                        if m.get("content"):
                            answer += m["content"]
                            yield ev(type="token", text=m["content"])
                        if d.get("done"):
                            ec, ed = d.get("eval_count", 0), d.get("eval_duration", 1)
                            stats = {"tokens": ec, "tps": round(ec / (ed / 1e9), 1) if ed else 0,
                                     "prompt_tokens": d.get("prompt_eval_count", 0),
                                     "load_s": round(d.get("load_duration", 0) / 1e9, 1),
                                     "total_s": round(time.time() - t0, 1)}
        except Exception as e:
            yield ev(type="error", text=str(e))
        finally:
            if answer or thinking:
                with db() as c:
                    c.execute("INSERT INTO messages(chat_id,role,content,thinking,sources,meta,created) VALUES(?,?,?,?,?,?,?)",
                              (cid, "assistant", answer, thinking, json.dumps(sources),
                               json.dumps({"model": preset["label"], "mode": mode, **stats}), time.time()))
                    c.execute("UPDATE chats SET updated=? WHERE id=?", (time.time(), cid))
        yield ev(type="done", stats=stats, model=preset["label"])

    return StreamingResponse(stream(), media_type="application/x-ndjson")


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def bind_check(host, solo=SOLO):
    """Solo mode has no login, so it must only ever be reachable from this machine. In Docker the app listens on all
    interfaces inside the container and the compose file publishes the port; STELLA80_BIND is that published address."""
    exposed = os.environ.get("STELLA80_BIND", host)
    if solo and exposed not in LOOPBACK:
        raise SystemExit(f"Solo mode has no login and would be reachable on {exposed}. Use mode: shared, or bind to 127.0.0.1.")


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST") or CFG.get("host") or ("127.0.0.1" if SOLO else "0.0.0.0")
    bind_check(host)
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT") or CFG.get("port", 8480)))
