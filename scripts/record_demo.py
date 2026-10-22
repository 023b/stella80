#!/usr/bin/env python3
"""Record a 60 to 90 second demo of the terminal UI against a running instance, with captions, then cut it with ffmpeg.

    # 1. a solo instance with the sample notes, processed by the agent, and Ollama with the models pulled
    # 2. python scripts/record_demo.py --url http://127.0.0.1:8480 --out docs/media

Playwright drives headless Chromium with video recording, so nothing on your desktop is captured. The recording is real time:
every answer is what the local model streamed. Waiting while a slow CPU model writes is sped up (and the caption says so);
nothing is faked or mocked. Outputs: demo.mp4 (1280x720), demo_square.mp4 (1080x1080, letterboxed), poster.png, and
demo-marks.json (when each step happened, for checking the cut).
Needs: pip install playwright && playwright install chromium, and ffmpeg on the PATH.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

Q_IN = "What happens in gradient descent if the learning rate is too large?"
Q_OUT = "Who won the 2018 FIFA World Cup?"
SPEEDUP = 20            # waiting for a CPU model to write is shown at this speed
KEEP_LIVE_S = 4         # seconds of each answer shown in real time before the speed-up starts
TAIL_S = 3              # and the last seconds, in real time

OVERLAY_JS = """
(() => {
  const css = `
  #__cur{position:fixed;z-index:2147483647;width:22px;height:22px;margin:-4px 0 0 -4px;border:2px solid #fff;border-radius:50%;
    background:rgba(255,181,71,.35);box-shadow:0 0 0 2px rgba(0,0,0,.6);pointer-events:none;transition:transform .08s;left:-50px;top:-50px}
  #__cur.down{transform:scale(.7);background:rgba(255,181,71,.8)}
  #__cap{position:fixed;z-index:2147483646;left:50%;bottom:22px;transform:translateX(-50%);max-width:86vw;padding:10px 22px;
    background:rgba(8,5,0,.86);border:1px solid #ffb547;border-radius:8px;color:#ffd68a;font:600 22px/1.3 'IBM Plex Mono',monospace;
    letter-spacing:.02em;text-align:center;opacity:0;transition:opacity .35s;pointer-events:none}
  #__cap.on{opacity:1}
  #__end{position:fixed;inset:0;z-index:2147483645;background:#000;display:none;flex-direction:column;align-items:center;justify-content:center;
    gap:18px;font-family:'VT323','IBM Plex Mono',monospace;color:#ffb547;text-shadow:0 0 8px rgba(255,170,40,.4);text-align:center}
  #__end b{font:400 84px 'Righteous',sans-serif;color:#e9e4d8;text-shadow:none}
  #__end b i{color:#c8561e;font-style:normal}
  #__end span{font-size:38px} #__end u{font-size:30px;color:#ffd68a;text-decoration:none}`;
  const mk = () => {
    if (document.getElementById('__cur') || !document.body) return;
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    const c = document.createElement('div'); c.id = '__cur';
    const k = document.createElement('div'); k.id = '__cap';
    const e = document.createElement('div'); e.id = '__end';
    e.innerHTML = '<b>STELLA<i>-</i>80</b><span>Local models. No cloud.</span><u>__REPO__</u>';
    document.body.append(c, k, e);
    addEventListener('mousemove', ev => { c.style.left = ev.clientX + 'px'; c.style.top = ev.clientY + 'px'; }, true);
    addEventListener('mousedown', () => c.classList.add('down'), true);
    addEventListener('mouseup', () => c.classList.remove('down'), true);
  };
  window.__cap = t => { mk(); const k = document.getElementById('__cap'); if (t) { k.textContent = t; k.classList.add('on'); } else k.classList.remove('on'); };
  window.__end = on => { mk(); document.getElementById('__end').style.display = on ? 'flex' : 'none'; };
  document.addEventListener('DOMContentLoaded', mk);
})();
"""


class Rec:
    def __init__(self, page):
        self.page, self.t0, self.marks = page, time.monotonic(), []
        self.x, self.y = 640, 360

    def mark(self, name):
        self.marks.append({"name": name, "t": round(time.monotonic() - self.t0, 2)})

    def wait(self, s):
        self.page.wait_for_timeout(int(s * 1000))

    def cap(self, text):
        self.page.evaluate("t => window.__cap(t)", text)

    def move(self, x, y, steps=28):
        self.page.mouse.move(x, y, steps=steps)
        self.x, self.y = x, y

    def click(self, locator, pause=0.5):
        locator.scroll_into_view_if_needed()
        b = locator.bounding_box()
        self.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        self.wait(0.25)
        self.page.mouse.down()
        self.wait(0.08)
        self.page.mouse.up()
        self.wait(pause)

    def type(self, text):
        self.page.keyboard.type(text, delay=38)

    def scroll(self, selector, to, ms=1600):
        self.page.evaluate("""([s, to, ms]) => new Promise(res => {
            const el = document.querySelector(s); const from = el.scrollTop; const t0 = performance.now();
            const step = now => { const k = Math.min(1, (now - t0) / ms); el.scrollTop = from + (to - from) * (k * k * (3 - 2 * k));
                if (k < 1) requestAnimationFrame(step); else res(); };
            requestAnimationFrame(step); })""", [selector, to, ms])

    def deck(self, want_open):
        if self.page.locator("#sw-strict").is_visible() != want_open:
            self.click(self.page.locator("#deck-toggle"), pause=0.7)

    def ask(self, question, tag, caption_live):
        """Type the question, run it, and mark first token and completion. Returns after the answer is finished."""
        self.click(self.page.locator("#input"))
        self.type(question)
        self.wait(0.6)
        self.mark(f"{tag}_send")
        self.click(self.page.locator("#send"), pause=0.2)
        self.page.wait_for_function("() => { const a = document.querySelector('.msg-bot:last-of-type .answer'); return a && a.textContent.trim().length > 20; }", timeout=600000)
        self.mark(f"{tag}_first_token")
        self.cap(caption_live)
        self.page.wait_for_function("() => document.querySelector('#send').textContent.trim() === 'RUN' && /TOK\\/S|\\d+\\.?\\d*S/.test((document.querySelector('.msg-bot:last-of-type .meta') || {}).textContent || '')", timeout=900000)
        self.mark(f"{tag}_done")


def story(r, args):
    p = r.page
    p.goto(args.url)
    p.wait_for_selector("#subjects button", state="attached", timeout=60000)
    r.wait(0.6)
    r.mark("intro")
    r.cap("STELLA-80: a study terminal for your own lecture notes")
    r.wait(1.8)
    # module selector (the control deck is folded away on a 720p screen; open it for this part)
    r.deck(True)
    r.cap("Pick a module. Everything below is searched in your vault only.")
    subs = p.locator("#subjects button")
    for i in range(min(subs.count(), 2)):
        b = subs.nth(i).bounding_box()
        r.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=14)
        r.wait(0.3)
    r.click(subs.nth(min(2, subs.count() - 1)), pause=0.6)
    # theme switch to amber via settings
    r.cap("Four screens: OLED, amber, green phosphor, paper")
    r.click(p.locator("#me-chip"))
    r.click(p.locator("#st-theme button[data-v=amber]"), pause=0.9)
    r.click(p.locator("#settings-close"), pause=0.6)
    r.mark("intro_end")
    # VAULT ONLY first: with it on, even a small local model cites reliably (docs/EVAL.md); in the default blend mode it
    # often writes a correct answer with no [n] markers at all, so the demo shows the mode that is measured to cite.
    r.cap("VAULT ONLY: answer from your notes, with numbered sources")
    r.click(p.locator("label.rocker:has(#sw-strict)"), pause=0.8)
    r.cap(None)
    # BRIEF mode (five lines) is much faster to record on a CPU; its long TEACH answers hit the token cap on a 4B model.
    # Use --mode teach with a larger model or a GPU.
    r.click(p.locator(f"#modes button[data-v={args.mode}]"), pause=0.3)
    r.deck(False)
    r.ask(Q_IN, "q1", "Every claim cited to a source (answer streaming, sped up)")
    if not p.locator(".msg-bot:last-of-type .cite").count():
        raise SystemExit("The answer carried no [n] citations (model output varies between runs). Nothing is faked: run the recording again.")
    r.cap("Every claim cited to a source")
    r.scroll("#log", 10000, 1400)
    r.wait(0.6)
    # citation -> note
    r.mark("cite")
    cite = p.locator(".msg-bot:last-of-type .cite").first
    r.cap(f"Click [{cite.get_attribute('data-n')}] to see the exact note it came from")
    r.click(cite, pause=0.8)
    card = p.locator(f".src[data-n='{cite.get_attribute('data-n')}']")
    r.click(card, pause=0.5)
    p.wait_for_selector("#printout:not([hidden])")
    r.wait(1.0)
    r.scroll("#printout", 420, 1600)
    r.wait(0.5)
    r.mark("cite_end")
    r.click(p.locator("#po-close"), pause=0.6)
    # VAULT ONLY
    r.cap("Ask something the notes do not cover")
    r.wait(1.0)
    r.ask(Q_OUT, "q2", "No source, no answer (sped up)")
    r.cap("No source, no answer")
    r.wait(1.8)
    # vault tab
    r.mark("vault")
    r.cap("The vault agent reads new files by itself. Progress and queue live here.")
    r.click(p.locator("#hub-open"), pause=0.6)
    r.click(p.locator("#hub-tabs [data-tab=vault]"), pause=1.4)
    r.scroll("#hub-body", 260, 1500)
    r.wait(0.9)
    p.keyboard.press("Escape")
    if p.locator("#hub").is_visible():
        r.click(p.locator("#hub .modal-head .pkey").first, pause=0.5)
    # printout of a processed note
    r.cap("What the agent writes: one note per lecture, linked to its concepts")
    pick = p.locator(".src.k-processed")
    card = pick.first if pick.count() else p.locator(".src").first
    r.click(card, pause=0.5)
    p.wait_for_selector("#printout:not([hidden])")
    r.wait(1.2)
    r.scroll("#printout", 380, 1700)
    r.wait(0.9)
    r.mark("vault_end")
    r.click(p.locator("#po-close"), pause=0.5)
    # end card
    r.cap(None)
    p.evaluate("window.__end(true)")
    r.mark("end")
    r.wait(5.0)
    r.mark("end_done")


def segments(marks, total):
    """Cut list [(start, end, speed)]: real time everywhere except the long wait inside each answer."""
    m = {x["name"]: x["t"] for x in marks}
    cuts, pos = [], 0.0
    for tag in ("q1", "q2"):
        a, b = m[f"{tag}_first_token"] + KEEP_LIVE_S, m[f"{tag}_done"] - TAIL_S
        if b - a > 6:
            cuts += [(pos, a, 1), (a, b, SPEEDUP)]
            pos = b
    cuts.append((pos, min(total, m["end_done"] + 0.5), 1))
    return [c for c in cuts if c[1] - c[0] > 0.05]


def assemble(raw, marks, out):
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(raw)], text=True))
    cuts = segments(marks, dur)
    parts = "".join(f"[0:v]trim=start={a:.2f}:end={b:.2f},setpts=(PTS-STARTPTS)/{s}[v{i}];" for i, (a, b, s) in enumerate(cuts))
    graph = parts + "".join(f"[v{i}]" for i in range(len(cuts))) + f"concat=n={len(cuts)}:v=1:a=0,fps=25,scale=1280:720,format=yuv420p[v]"
    enc = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", "-an"]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-filter_complex", graph, "-map", "[v]", *enc, str(out / "demo.mp4")], check=True)
    # square for the feed: the whole 16:9 screen on a black 1080x1080 canvas (cropping would cut off the sidebars)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / "demo.mp4"), "-vf", "scale=1080:-2,pad=1080:1080:0:(oh-ih)/2:black,format=yuv420p",
                    *enc, str(out / "demo_square.mp4")], check=True)
    cite_t = next(x["t"] for x in marks if x["name"] == "cite_end")
    return cuts, dur, cite_t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8480")
    ap.add_argument("--out", default="docs/media")
    ap.add_argument("--repo", default="github.com/023b/stella80")
    ap.add_argument("--mode", default="brief", help="study mode to demonstrate (teach, exam, brief, ...)")
    ap.add_argument("--keep-raw", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found on the PATH")
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 720}, record_video_dir=tmp, record_video_size={"width": 1280, "height": 720}, device_scale_factor=1)
        ctx.add_init_script(OVERLAY_JS.replace("__REPO__", args.repo))
        page = ctx.new_page()
        r = Rec(page)
        try:
            story(r, args)
        except Exception:
            page.screenshot(path=str(out / "demo-failure.png"))
            raise
        finally:
            ctx.close()
            browser.close()
        raw = next(Path(tmp).glob("*.webm"))
        if args.keep_raw:
            shutil.copy(raw, out / "demo-raw.webm")
        (out / "demo-marks.json").write_text(json.dumps(r.marks, indent=1))
        cuts, dur, cite_t = assemble(raw, r.marks, out)
    final = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out / "demo.mp4")], text=True))
    # poster: the citation moment, found in the cut timeline
    t, poster_t = 0.0, 0.0
    for a, b, s in cuts:
        if a <= cite_t <= b:
            poster_t = t + (cite_t - a) / s
            break
        t += (b - a) / s
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0, poster_t - 6):.2f}", "-i", str(out / "demo.mp4"), "-frames:v", "1", str(out / "poster.png")], check=True)
    print(f"raw {dur:.0f}s -> demo.mp4 {final:.0f}s; cuts: {[(round(a), round(b), s) for a, b, s in cuts]}")


if __name__ == "__main__":
    main()
