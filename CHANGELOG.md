# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/), and versions follow
[Semantic Versioning](https://semver.org/) once 1.0 is reached; until then minor versions may change configuration.

## [0.1.0] - unreleased

First public release. Extracted from the author's own deployment (one mini PC, one student and three friends).

### Added
- **Vault agent**: turns PDFs, slides, Word documents, notebooks, spreadsheets, code and images into an Obsidian knowledge
  graph with concept notes, course maps and cross-course links. Every concept needs a quote that is found in the source text,
  or it is dropped. A vision model reads slide figures. A `_raw/` copy of every file's text is written with no AI involved.
- **Study terminal**: FastAPI and vanilla JS web app with hybrid retrieval (SQLite FTS5 BM25 plus embeddings, rank fusion),
  streamed answers with numbered citations, eight study modes, VAULT ONLY, LaTeX, Plotly plots and Mermaid diagrams,
  eight tutor personas, amber, green, paper and OLED themes. All JavaScript libraries and fonts are bundled; no CDN calls.
- **Solo and shared modes**. Solo: no login, loopback only. Shared: per-person scrypt passphrases, rate-limited login, admin console
  with per-person limits, group chat, leaderboard, study log, uploads into the processing queue.
- **Docker Compose** and **bare metal (systemd user units)** installs; `scripts/init.sh` and a sample lecture set.
- **Tests and evaluation**: 41 offline tests (retrieval, quote verification, authentication), CI on Python 3.11 and 3.12, and
  `scripts/eval.py` with `docs/EVAL.md` (retrieval hit@k per ranker, citation support, VAULT ONLY refusals, latency).
- Architecture diagram rendered from `docs/architecture.mmd` by `scripts/render_diagram.py`.

### Changed
- `retrieve()` takes a `mode` (hybrid, keyword, embeddings) so each ranker can be measured alone.
- The solo-mode "refuse to listen beyond loopback" check is a function, `bind_check()`, so it can be tested.

### Known limitations
See `docs/RELEASING.md` for the list that is tracked as issues.
