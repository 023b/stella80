#!/usr/bin/env python3
"""Measure STELLA-80 on a set of questions: retrieval, citations, VAULT ONLY refusals, speed.

    python scripts/eval.py                       # sample notes, eval/questions.yaml, results into docs/EVAL.md
    python scripts/eval.py --skip-generation     # retrieval only (needs the embedding model, not the chat model)
    python scripts/eval.py --notes ~/my-notes --questions my-questions.yaml --out /tmp/EVAL.md

It builds a throw-away copy of the notes in a temporary folder, indexes it with the real embedding model, then asks the real
app over HTTP. Nothing is mocked and nothing in your own data folders is touched. Anything that could not be measured is
written as "not measured". See docs/EVAL.md for what each number means.
"""
import argparse
import datetime
import importlib.util
import json
import platform
import re
import shutil
import socket
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
MODES = ("keyword", "embeddings", "hybrid")
KS = (1, 3, 5)
NM = "not measured"

# Phrases a VAULT ONLY answer uses when the notes do not cover the question. A regex is a blunt instrument; the per-question
# answers are saved next to the table (docs/eval-results.json) so the classification can be checked by eye.
REFUSAL = re.compile(
    r"\b(?:do(?:es)? not|don'?t|doesn'?t|cannot|can'?t|unable to|no)\b[^.\n]{0,70}\b(?:contain|mention|cover|include|provide|have|find|address|discuss|say|information|answer|excerpts?)\b"
    r"|\bnot (?:covered|mentioned|found|present|included|in (?:the|your|these) (?:excerpts?|notes|vault|material))\b"
    r"|\b(?:outside|beyond) (?:the|your) (?:excerpts?|notes|vault|scope)\b", re.I)


def norm(s):
    return re.sub(r"\s+", " ", s.lower()).strip()


def pct(a, b):
    return NM if not b else f"{100 * a / b:.0f}% ({a}/{b})"


def load_app(workdir, notes, ollama, model, embed_model):
    """Import stella80/app.py against a temporary solo-mode config whose only vault is a copy of the notes."""
    vault = workdir / "vaults" / "eval-vault"
    for p in Path(notes).rglob("*.md"):
        dest = vault / p.relative_to(notes)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
    engine = {"model": model, "host": "study", "vision": False}
    cfg = {"mode": "solo", "data_dir": str(workdir / "data"), "timezone": "UTC", "ram_guard": False, "embed_model": embed_model,
           "ollama": {"study": ollama, "background": ollama}, "engines": {"quick": engine, "scholar": engine, "deep": engine},
           "vaults": [{"path": str(vault), "title": "Eval vault"}]}
    (workdir / "stella80.yaml").write_text(yaml.safe_dump(cfg))
    import os
    os.environ["STELLA80_CONFIG"] = str(workdir / "stella80.yaml")
    os.environ["STELLA80_BIND"] = "127.0.0.1"
    spec = importlib.util.spec_from_file_location("s80_eval_app", REPO / "stella80" / "app.py")
    app = importlib.util.module_from_spec(spec)
    sys.modules["s80_eval_app"] = app
    spec.loader.exec_module(app)
    app.init_db()
    app.reindex()
    if app.index_state["error"]:
        raise SystemExit(f"Indexing failed: {app.index_state['error']} (is Ollama up with the embedding model pulled?)")
    return app, "eval-vault"


def retrieval_eval(app, vault, qs):
    res = {m: {"chunk": {k: 0 for k in KS}, "file": {k: 0 for k in KS}} for m in MODES}
    per_q = []
    for q in qs:
        row = {"id": q["id"]}
        sup = [norm(s) for s in q["support"]]
        for m in MODES:
            hits = app.retrieve(q["question"], "ALL", max(KS), vault, mode=m)
            row[m] = [Path(h["path"]).name for h in hits]
            for k in KS:
                top = hits[:k]
                res[m]["chunk"][k] += any(s in norm(h["text"]) for h in top for s in sup)
                res[m]["file"][k] += any(Path(h["path"]).name in q["sources"] for h in top)
        per_q.append(row)
    return res, per_q


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app):
    import uvicorn
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server, f"http://127.0.0.1:{port}"
        time.sleep(0.1)
    raise SystemExit("The app did not start.")


def ask(base, question, grounding):
    body = {"message": question, "subject": "ALL", "mode": "brief", "depth": "brief", "grounding": grounding, "model": "quick",
            "think": False, "visuals": False}
    t0, ttft, answer, sources, stats, err = time.time(), None, "", [], {}, None
    with httpx.stream("POST", base + "/api/chat", json=body, timeout=httpx.Timeout(900, connect=10)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            ev = json.loads(line)
            if ev["type"] == "token":
                ttft = ttft if ttft is not None else time.time() - t0
                answer += ev["text"]
            elif ev["type"] == "sources":
                sources = ev["sources"]
            elif ev["type"] == "done":
                stats = ev.get("stats") or {}
            elif ev["type"] == "error":
                err = ev["text"]
    return {"answer": answer, "sources": sources, "stats": stats, "error": err, "wall_s": time.time() - t0, "ttft_s": ttft}


def cited_numbers(answer):
    out = set()
    for grp in re.findall(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]", answer):
        for part in re.split(r"\s*,\s*", grp):
            m = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
            out.update(range(int(m.group(1)), int(m.group(2)) + 1) if m else [int(part)] if part.isdigit() else [])
    return out


def generation_eval(app, vault, base, qs, grounding):
    rows = []
    for i, q in enumerate(qs, 1):
        print(f"[{i}/{len(qs)}] {q['id']}", flush=True)
        a = ask(base, q["question"], grounding)
        row = {"id": q["id"], "scope": q["scope"], "question": q["question"], **a}
        row["refused"] = bool(REFUSAL.search(a["answer"]))
        if q["scope"] == "in":
            by_snip = {h["text"][:280]: h for h in app.retrieve(q["question"], "ALL", 5, vault)}
            texts = {s["n"]: norm(by_snip[s["snippet"]]["text"]) if s["snippet"] in by_snip else norm(s["snippet"]) for s in a["sources"]}
            sup = [norm(x) for x in q["support"]]
            nums = cited_numbers(a["answer"])
            row["cited"] = sorted(nums)
            row["valid"] = sorted(n for n in nums if n in texts)
            row["supported"] = sorted(n for n in row["valid"] if any(s in texts[n] for s in sup))
        rows.append(row)
    return rows


def quantile(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def summarise(rows):
    inn = [r for r in rows if r["scope"] == "in" and not r["error"]]
    out = [r for r in rows if r["scope"] == "out" and not r["error"]]
    answered = [r for r in inn if not r["refused"]]
    valid, supported = sum(len(r["valid"]) for r in answered), sum(len(r["supported"]) for r in answered)
    cited_any = sum(bool(r["valid"]) for r in answered)
    lat = [r["wall_s"] for r in rows if not r["error"]]
    ttft = [r["ttft_s"] for r in rows if not r["error"] and r["ttft_s"] is not None]
    tps = [r["stats"]["tps"] for r in rows if r["stats"].get("tps")]
    toks = [r["stats"]["tokens"] for r in rows if r["stats"].get("tokens")]
    return {
        "in_scope_answered_with_a_valid_citation": pct(cited_any, len(answered)),
        "citations_pointing_at_supporting_text": pct(supported, valid),
        "citations_to_a_source_that_does_not_exist": f"{sum(len(r['cited']) - len(r['valid']) for r in answered)} of {sum(len(r['cited']) for r in answered)}" if answered else NM,
        "in_scope_questions_wrongly_refused": pct(sum(r["refused"] for r in inn), len(inn)),
        "out_of_scope_questions_refused": pct(sum(r["refused"] for r in out), len(out)),
        "out_of_scope_answers_that_still_cite_sources": pct(sum(bool(cited_numbers(r["answer"])) for r in out), len(out)),
        "latency_median_s": f"{statistics.median(lat):.0f}" if lat else NM,
        "latency_p90_s": f"{quantile(lat, 0.9):.0f}" if lat else NM,
        "first_token_median_s": f"{statistics.median(ttft):.1f}" if ttft else NM,
        "tokens_per_second_mean": f"{statistics.mean(tps):.1f}" if tps else NM,
        "answer_tokens_mean": f"{statistics.mean(toks):.0f}" if toks else NM,
        "n_in": len(inn), "n_out": len(out), "n_errors": sum(bool(r["error"]) for r in rows),
    }


def cpu_name():
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or NM


def ram_gb():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                return f"{int(line.split()[1]) / 1048576:.0f} GB"
    except OSError:
        pass
    return NM


def render(args, ret, gen, n_notes, n_chunks, n_in, n_out, model_ok):
    L = [f"Run on {datetime.date.today().isoformat()}. Hardware: {cpu_name()}, {ram_gb()} RAM, {'CPU only' if args.cpu else 'accelerator not recorded'}. "
         f"Answer model: `{args.model}`. Embedding model: `{args.embed_model}`. Notes indexed: {n_notes} files, {n_chunks} chunks. "
         f"Questions: {n_in} in scope, {n_out} out of scope. One run per question at the app's normal answer temperature (0.4), so repeat runs will vary.", ""]
    L += ["### Retrieval: does the right passage appear in the top k?", "",
          "| Ranker | hit@1 | hit@3 | hit@5 |", "|---|---|---|---|"]
    for m in MODES:
        label = {"keyword": "Keyword only (BM25)", "embeddings": "Embeddings only", "hybrid": "Hybrid (BM25 + embeddings, rank fusion)"}[m]
        L.append(f"| {label} | " + " | ".join(pct(ret[m]["chunk"][k], n_in) for k in KS) + " |")
    L += ["", "Passage-level: a retrieved chunk counts if it contains one of the question's `support` phrases.", "",
          "| Ranker | file hit@1 | file hit@3 | file hit@5 |", "|---|---|---|---|"]
    for m in MODES:
        L.append(f"| {m} | " + " | ".join(pct(ret[m]["file"][k], n_in) for k in KS) + " |")
    L += ["", "File-level: the expected note appears in the top k.", "", "### Answers with VAULT ONLY on", ""]
    if gen is None:
        why = "skipped with --skip-generation" if args.skip_generation else f"model {args.model} not available" if not model_ok else "generation did not run"
        L += [f"{NM} ({why}).", ""]
    else:
        L += ["| Measure | Result |", "|---|---|"]
        names = [("in_scope_answered_with_a_valid_citation", "In-scope answers with at least one valid [n] citation"),
                 ("citations_pointing_at_supporting_text", "Citations whose source contains the supporting text"),
                 ("citations_to_a_source_that_does_not_exist", "Citations to a source number that does not exist"),
                 ("in_scope_questions_wrongly_refused", "In-scope questions refused"),
                 ("out_of_scope_questions_refused", "Out-of-scope questions refused"),
                 ("out_of_scope_answers_that_still_cite_sources", "Out-of-scope answers that carry [n] markers (see note below)"),
                 ("latency_median_s", "Latency, median, question to last token (s)"), ("latency_p90_s", "Latency, 90th percentile (s)"),
                 ("first_token_median_s", "Time to first token, median (s)"), ("tokens_per_second_mean", "Generation speed, mean (tokens/s)"),
                 ("answer_tokens_mean", "Answer length, mean (tokens)")]
        L += [f"| {label} | {gen[k]} |" for k, label in names]
        if gen["n_errors"]:
            L += ["", f"{gen['n_errors']} question(s) failed with an error and are left out of these numbers."]
        L += [""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--notes", default=str(REPO / "examples" / "sample-src"))
    ap.add_argument("--questions", default=str(REPO / "eval" / "questions.yaml"))
    ap.add_argument("--out", default=str(REPO / "docs" / "EVAL.md"))
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="qwen3.5:4b")
    ap.add_argument("--embed-model", default="nomic-embed-text")
    ap.add_argument("--grounding", default="strict", choices=["strict", "blend"], help="strict = the VAULT ONLY switch")
    ap.add_argument("--skip-generation", action="store_true")
    ap.add_argument("--cpu", action="store_true", help="say in the report that the model ran on the CPU")
    ap.add_argument("--limit", type=int, default=0, help="only the first N questions (a quick trial)")
    args = ap.parse_args()

    qs = yaml.safe_load(Path(args.questions).read_text())["questions"]
    if args.limit:
        qs = qs[:args.limit]
    ins = [q for q in qs if q["scope"] == "in"]

    with tempfile.TemporaryDirectory(prefix="stella80-eval-") as tmp:
        app, vault = load_app(Path(tmp), args.notes, args.ollama, args.model, args.embed_model)
        with app.db() as c:
            n_notes = c.execute("SELECT COUNT(DISTINCT path) FROM chunks").fetchone()[0]
            n_chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        ret, per_q = retrieval_eval(app, vault, ins)
        gen, rows = None, None
        try:
            tags = httpx.get(args.ollama + "/api/tags", timeout=10).json()["models"]
            model_ok = any(m["name"] == args.model or m["name"] == args.model + ":latest" for m in tags)
        except Exception:
            model_ok = False
        if not args.skip_generation and model_ok:
            server, base = serve(app)
            try:
                rows = generation_eval(app, vault, base, qs, args.grounding)
            finally:
                server.should_exit = True
            gen = summarise(rows)
        elif not args.skip_generation:
            print(f"Model {args.model} is not available at {args.ollama}; generation metrics are not measured.", file=sys.stderr)

    body = render(args, ret, gen, n_notes, n_chunks, len(ins), len(qs) - len(ins), model_ok)
    out = Path(args.out)
    start, end = "<!-- results:start -->", "<!-- results:end -->"
    text = out.read_text() if out.exists() else f"{start}\n{end}\n"
    if start not in text or end not in text:
        raise SystemExit(f"{out} has no {start} ... {end} markers.")
    out.write_text(text[:text.index(start) + len(start)] + "\n\n" + body + "\n" + text[text.index(end):])
    raw = out.with_name("eval-results.json")
    raw.write_text(json.dumps({"date": datetime.date.today().isoformat(), "model": args.model, "embed_model": args.embed_model,
                               "grounding": args.grounding, "retrieval": per_q, "answers": rows}, indent=1, ensure_ascii=False) + "\n")
    print(body)
    print(f"\nWrote {out} and {raw}")


if __name__ == "__main__":
    main()
