# Releasing

## Tag v0.1.0 (not done yet)

Do this from `main` after the `polish/linkedin` branch is merged and CI is green.

```bash
git checkout main && git pull
# 1. set the release date in CHANGELOG.md ("## [0.1.0] - unreleased" becomes "## [0.1.0] - YYYY-MM-DD") and commit it
git commit -am "Release 0.1.0"
git tag -a v0.1.0 -m "STELLA-80 0.1.0: first public release"
git push origin main v0.1.0        # publishing: only when you are ready
```

Then on GitHub: Releases, Draft a new release, choose the tag `v0.1.0`, paste the 0.1.0 section of `CHANGELOG.md`, and attach
`docs/media/demo.mp4`. Make the repository public first (Settings, General, Danger Zone) if it is still private.

Before making the repository public, check once more for anything personal: `git log -p | grep -i -E "password|token|secret"`
should only show code that handles them, and `git ls-files` should list no `.env`, `stella80.yaml`, `data/` or `vaults/`.

## Issues to open for known limitations

Create these on GitHub (they describe real gaps, not planned marketing):

1. **Split `stella80/app.py` into modules.** It is a single file of about 2,100 lines (retrieval, auth, admin, queue, chat).
   Move the routes into routers and the database code into its own module. The test suite is the safety net for the move.
2. **Parallel answers in shared mode.** Answers run one at a time (a queue, one Ollama slot). Support `OLLAMA_NUM_PARALLEL`
   slots, with the memory cost per slot documented, and let the queue admit as many people as there are slots.
3. **Check citation faithfulness, not just relevance.** The eval checks that a cited passage contains the supporting phrase;
   it does not check that the sentence beside the citation is a faithful paraphrase. Add an overlap or entailment check and
   grow `eval/questions.yaml` to 30 to 50 questions over a larger public vault.
4. **Windows and macOS.** Only Linux is tested. Docker on macOS should work and bare metal on Windows is untested. Add them to
   CI (at least the test job) and document what works.
5. **HTTPS for shared mode.** Shared mode has no TLS of its own and relies on Tailscale or a reverse proxy. Ship a Compose
   profile with Caddy, and a short guide for the Tailscale route.

You can delete this section once the issues exist.
