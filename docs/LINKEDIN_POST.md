<!-- Draft. Before posting: make the repository public, attach docs/media/demo_square.mp4 (feed) or demo.mp4, and re-check the
     number below against docs/EVAL.md if you have rerun the eval. -->

I built a study computer that has read all my lecture notes, and it refuses to answer when the notes don't cover the question.

STELLA-80 runs entirely on my own machine: local models through Ollama, no cloud, no API keys, no telemetry. A background agent turns lecture PDFs, slides, notebooks and spreadsheets into an Obsidian knowledge graph. A terminal-style web UI answers from that vault with numbered citations you can click to open the source note.

Three things I cared about getting right:

- Concepts have to earn their place. The agent asks the model for a supporting quote with every concept, checks the quote against the source text, and drops the concept if it isn't there.
- Retrieval is hybrid: SQLite FTS5 (BM25) fused with embeddings by reciprocal rank, with my own notes weighted above generated ones. Each ranker can be measured alone.
- VAULT ONLY mode answers from the notes or says so. No source, no answer.

A measured result, small and honest: on a 10-question test set over three sample lectures (7 answerable, 3 not), with qwen3.5:4b on a CPU-only laptop, VAULT ONLY refused 3 of 3 unanswerable questions and wrongly refused 0 of 7 answerable ones. The set is tiny, so the harness matters more than the number. It is in the repo, with its limits written down next to the results, including where hybrid retrieval lost to keyword search.

It is a personal tool used daily by four people on one mini PC, with 52 offline tests and CI. Not a product.

Code, evaluation and a 75-second demo: https://github.com/023b/stella80

#LocalLLM #Ollama #RAG #Obsidian #OpenSource #Python
