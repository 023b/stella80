# The vault agent

`agent/vault_agent.py` turns a folder of course files into an Obsidian knowledge graph using local models. It runs
once and exits. A timer, or the loop in `compose.yaml`, starts it every 10 minutes, and each run only does new work.

## What it writes

```
vault/                          open this folder in Obsidian
  Study-Vault/
    Machine-Learning/           your own notes: read and linked, never edited
    _auto/
      concepts/                 one note per concept: definition, where it appears (with quotes), related concepts
      sources/<subject>/        one note per file: section summaries, the concepts in each, figures from slides
      maps/                     a concept map per subject
      figures/                  slide images the vision model found worth keeping
      Knowledge graph.md        subjects, plus the concepts that bridge more than one
      Status.md                 progress, last run
    _raw/<subject>/             full extracted text of every file, with no AI involved, for checking
```

Generated notes end with a marker line. Anything you write below
`<!-- user-notes: anything below this line is preserved -->` survives every re-render.

## How a file is processed

1. **Scan.** New or changed files under `src_root` are hashed and queued. Duplicate copies are skipped. A file
   younger than one minute is left alone, since it may still be syncing. If more than a quarter of the known files
   vanish at once, the agent treats it as a sync problem and keeps its state.
2. **Extract.** It reads pdfminer for PDFs (Ghostscript repairs damaged ones), MarkItDown for pptx and docx,
   notebook cells with their outputs, the head and row count of CSV and Excel files, code as fenced blocks, and
   images through the vision model.
3. **Text pass.** Each chunk of about 5,000 characters goes to `chat_model` with a JSON schema: a short summary and
   a few concepts (`max_concepts_per_chunk`), each with a definition and a verbatim quote. The quote is checked against the chunk, and
   a concept whose quote isn't really in the text is dropped. Names are merged through aliases in `rules.yaml` and
   by embedding similarity. Near-duplicate chunks reuse the earlier result with no model call.
4. **Vision pass.** Once no text is pending, PDF pages with large unique images, or with almost no text, are
   rendered and sent to `vision_model`. Diagrams, plots, equations and tables become figure sections with their
   concepts.
5. **Render.** `_auto/` is rebuilt from the database. A concept gets its own note only once it appears in at least
   two documents or four passages, so one-off noise stays out of the graph.
6. **Back up.** The vault is committed to git, and a daily snapshot of `state.db` is kept (the last 7).

## Tuning: `agent/rules.yaml`

Read at the start of every run, so there is nothing to restart.

- `instructions`: free text added to every prompt. Use it to say what a good concept looks like for your subject.
- `ignore`: names never to create (*introduction*, *exercise*, ...).
- `aliases`: force variants together, for example `Convolutional neural network: [CNN, ConvNet]`.
- `graph`: how much evidence a concept needs, and how many links each note gets.
- `throttle`: pauses between calls, the load-average ceiling and the image DPI. Raise the pauses on a machine
  people also use for other things.

## Commands

```bash
VAULT_AGENT_CONFIG=vaults/main/agent.yaml python agent/vault_agent.py run      # scan, process, render, back up
VAULT_AGENT_CONFIG=vaults/main/agent.yaml python agent/vault_agent.py render   # re-render only, no model
VAULT_AGENT_CONFIG=vaults/main/agent.yaml python agent/vault_agent.py status   # counts
python agent/vault_agent.py run --limit 3                                      # at most 3 documents this run
```

## Rebuilding

`_auto/` can be deleted at any time; the next run renders it again from `state/state.db`. To read everything
again from scratch, stop the agent and delete `state/state.db`.

## Without the agent

A vault listed in `stella80.yaml` with `path:` only is searched as it is. That is useful for an Obsidian vault you
already keep by hand. Uploads and the processing queue need an agent, so they are hidden for such vaults.
