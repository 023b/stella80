# Evaluation

`scripts/eval.py` measures four things on a set of questions: whether retrieval finds the right passage, whether the
citations in an answer point at text that supports it, whether the VAULT ONLY switch refuses what the notes cannot answer,
and how fast answers arrive. The numbers below were produced by that script on the machine described in the results. Anything
the script could not measure says **not measured**. Nothing here is estimated or copied from elsewhere.

```bash
python scripts/eval.py                    # sample notes + eval/questions.yaml, rewrites the results block below
python scripts/eval.py --skip-generation  # retrieval only: needs the embedding model, not the chat model
```

It copies the notes into a temporary folder, indexes them with the real embedding model, starts the real app on a free local
port, and asks it every question over HTTP, with VAULT ONLY on, BRIEF mode and visuals off. Your own data folders are not touched.
The per-question answers and retrieved files are saved to [eval-results.json](eval-results.json) so every number can be checked.

## What the numbers mean

| Measure | Definition |
|---|---|
| hit@k (passage) | For an in-scope question, one of its top k retrieved chunks contains at least one of the question's `support` phrases (case and spacing ignored). |
| hit@k (file) | One of the top k chunks comes from the note the question lists under `sources`. Easier than passage-level: a note has several chunks. |
| Keyword only | SQLite FTS5 with BM25 ranking, nothing else. |
| Embeddings only | Cosine similarity against the stored chunk vectors, nothing else. |
| Hybrid | The app's default: both rankers' top 40, fused by reciprocal rank, your own notes weighted above processed and raw ones, at most 3 chunks per file. |
| Valid citation | An `[n]` in the answer where n is one of the numbered sources the app sent for that question. |
| Citation supports | The chunk behind a valid `[n]` contains a `support` phrase for that question. This checks that the cited passage is relevant and says what the question needs. It does not prove that the sentence next to the citation is a faithful paraphrase of it. |
| Refused | The answer matches a list of refusal phrases (for example "the excerpts do not contain"). That is a regular expression, so it can misclassify. Read `eval-results.json` before quoting a refusal rate. |
| Carries [n] markers | The answer contains `[n]` at all. A VAULT ONLY refusal often lists what the excerpts do cover and tags them `[1][2]...`; those markers do not back a claim, so this row is not a hallucination rate. |
| Latency | Wall-clock time from sending the question to the last token, including search and any model load. First-token time is shown separately. |
| Tokens/s | Ollama's own eval count over its eval duration for the answer. |

## Results

<!-- results:start -->

Run on 2026-10-22. Hardware: Intel(R) Core(TM) Ultra 5 226V, 15 GB RAM, CPU only. Answer model: `qwen3.5:4b`. Embedding model: `nomic-embed-text`. Notes indexed: 3 files, 15 chunks. Questions: 7 in scope, 3 out of scope. One run per question at the app's normal answer temperature (0.4), so repeat runs will vary.

### Retrieval: does the right passage appear in the top k?

| Ranker | hit@1 | hit@3 | hit@5 |
|---|---|---|---|
| Keyword only (BM25) | 43% (3/7) | 100% (7/7) | 100% (7/7) |
| Embeddings only | 57% (4/7) | 86% (6/7) | 86% (6/7) |
| Hybrid (BM25 + embeddings, rank fusion) | 71% (5/7) | 86% (6/7) | 86% (6/7) |

Passage-level: a retrieved chunk counts if it contains one of the question's `support` phrases.

| Ranker | file hit@1 | file hit@3 | file hit@5 |
|---|---|---|---|
| keyword | 100% (7/7) | 100% (7/7) | 100% (7/7) |
| embeddings | 71% (5/7) | 100% (7/7) | 100% (7/7) |
| hybrid | 100% (7/7) | 100% (7/7) | 100% (7/7) |

File-level: the expected note appears in the top k.

### Answers with VAULT ONLY on

| Measure | Result |
|---|---|
| In-scope answers with at least one valid [n] citation | 100% (7/7) |
| Citations whose source contains the supporting text | 60% (6/10) |
| Citations to a source number that does not exist | 0 of 10 |
| In-scope questions refused | 0% (0/7) |
| Out-of-scope questions refused | 100% (3/3) |
| Out-of-scope answers that carry [n] markers (see note below) | 100% (3/3) |
| Latency, median, question to last token (s) | 35 |
| Latency, 90th percentile (s) | 42 |
| Time to first token, median (s) | 29.2 |
| Generation speed, mean (tokens/s) | 9.9 |
| Answer length, mean (tokens) | 71 |

<!-- results:end -->

## Notes on the run above (2026-10-22)

Read from `eval-results.json`; they describe that run only and are not regenerated.

- **Hybrid did not beat keyword-only at k of 3 or 5.** On this tiny set hybrid is best at hit@1 (5 of 7 against 3 of 7 for
  keyword and 4 of 7 for embeddings), but one passage-level miss at k=3 and k=5 (the question about training memory against
  inference memory) was found by keyword search and lost in the fusion. One question is 14 points, so this is an observation,
  not a conclusion.
- **That same question explains the weakest citation number.** Its answer cited three sources, none containing the supporting
  sentence, so 6 of 10 citations support the question and the other 4 are on-topic passages that do not. Six of the seven
  answers were fully supported.
- **The refusals are real.** All three out-of-scope answers say the excerpts do not contain the answer, including the one on
  attention in transformers, which is close to the notes' topic. No in-scope question was refused. I read all ten answers to
  check the refusal classification; it was right on this run.
- **Speed is a CPU number.** The 29 s median to first token is mostly the model reading its roughly 1,000-token prompt on the CPU
  (and, on the first question, loading the model). The answers are short because BRIEF mode was used (71 tokens on average).

## Limits of this evaluation

- **The sample set is tiny.** Three short lectures and ten questions. Hit@k saturates quickly on a vault this small, and one
  question moves a percentage by 14 points. The harness is the deliverable; the numbers are only meaningful on a larger set.
  Put 30 to 50 questions over a larger vault in `eval/questions.yaml` and rerun.
- **One run, one model, one machine.** Answers are sampled at temperature 0.4, so a second run will differ. Speed depends entirely
  on the hardware listed with the results.
- **Citation support is a relevance check**, not a faithfulness check (see the table above).
- **Out-of-scope questions are few** and one of them is deliberately close to the notes' topic, which is the hard case.
- The sample notes are short lecture handouts written for this repository, not real course material.

## Adding questions

`eval/questions.yaml` documents the format at the top of the file. Each in-scope question needs the file name of the note it
should come from and one or more short phrases copied from the supporting passage. Out-of-scope questions need only the text.
