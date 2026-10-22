"""Hybrid retrieval: BM25 (SQLite FTS5) fused with embedding similarity. The embedding model is a deterministic stand-in
(see conftest.fake_vectors), so these tests run offline and in well under a second."""
import pytest

from conftest import make_app, write_note

CAR = "# Engines\n\nThe automobile engine converts fuel into motion through repeated combustion cycles in each cylinder.\n"
KEYWORD_ONLY = "# Spectra\n\nThe Hessian eigenvalue spectrum controls how quickly an iterative method converges near a minimum.\n"
BOTH = "# Vehicles\n\nA car is a road vehicle with an engine; the car engine drives the wheels through a gearbox and a shaft.\n"
OTHER = "# Cooking\n\nBoil the pasta for ten minutes, drain it, then stir in the sauce and a spoon of olive oil before serving.\n"


@pytest.fixture
def idx(tmp_path):
    app = make_app(tmp_path, "solo", ("VaultA", "VaultB"))
    a, b = tmp_path / "VaultA", tmp_path / "VaultB"
    write_note(a, "ABC101-Alpha/engines.md", CAR)
    write_note(a, "ABC101-Alpha/spectra.md", KEYWORD_ONLY)
    write_note(a, "ABC101-Alpha/vehicles.md", BOTH)
    write_note(a, "DEF202-Beta/cooking.md", OTHER)
    write_note(b, "GHI303-Gamma/secret.md", "# Engines\n\nThe automobile engine in the other vault is private and must never appear in VaultA results.\n")
    app.reindex()
    assert app.index_state["error"] is None
    return app


def paths(hits):
    return [h["path"] for h in hits]


def test_keyword_ranker_needs_the_word(idx):
    hits = idx.retrieve("Hessian", "ALL", 5, "VaultA", mode="keyword")
    assert paths(hits)[0] == "VaultA/ABC101-Alpha/spectra.md"
    assert idx.retrieve("car", "ALL", 5, "VaultA", mode="keyword")[0]["path"] == "VaultA/ABC101-Alpha/vehicles.md"
    # "automobile" never contains the word "car": the keyword ranker cannot reach engines.md from it
    assert "VaultA/ABC101-Alpha/engines.md" not in paths(idx.retrieve("car", "ALL", 5, "VaultA", mode="keyword"))


def test_embedding_ranker_finds_a_synonym(idx):
    hits = idx.retrieve("car", "ALL", 5, "VaultA", mode="embeddings")
    assert "VaultA/ABC101-Alpha/engines.md" in paths(hits)


def test_hybrid_has_what_either_ranker_finds(idx):
    got = paths(idx.retrieve("car", "ALL", 5, "VaultA", mode="hybrid"))
    assert "VaultA/ABC101-Alpha/engines.md" in got       # only the embedding ranker sees this one
    assert "VaultA/ABC101-Alpha/vehicles.md" in got      # both rankers see this one


def test_agreement_between_rankers_wins(idx):
    # vehicles.md contains the word and is near the query vector, so fusion puts it ahead of the embedding-only hit
    got = paths(idx.retrieve("car", "ALL", 5, "VaultA"))
    assert got.index("VaultA/ABC101-Alpha/vehicles.md") < got.index("VaultA/ABC101-Alpha/engines.md")


def test_rare_keyword_survives_fusion(idx):
    assert paths(idx.retrieve("Hessian eigenvalue", "ALL", 3, "VaultA"))[0] == "VaultA/ABC101-Alpha/spectra.md"


def test_vault_isolation(idx):
    for mode in ("hybrid", "keyword", "embeddings"):
        assert all(p.startswith("VaultA/") for p in paths(idx.retrieve("automobile engine", "ALL", 10, "VaultA", mode=mode)))
    assert paths(idx.retrieve("automobile engine", "ALL", 10, "VaultB")) == ["VaultB/GHI303-Gamma/secret.md"]


def test_subject_filter(idx):
    hits = idx.retrieve("pasta olive oil", "ABC101", 10, "VaultA")
    assert all("/ABC101-Alpha/" in p for p in paths(hits))
    assert "VaultA/DEF202-Beta/cooking.md" in paths(idx.retrieve("pasta olive oil", "DEF202", 10, "VaultA"))


def test_your_own_notes_outrank_raw_dumps(tmp_path):
    app = make_app(tmp_path, "solo", ("VaultA",))
    text = "# Gradient\n\nGradient descent is an optimiser that repeatedly steps against the slope of the loss function.\n"
    write_note(tmp_path / "VaultA", "ABC101-Alpha/_raw/dump.md", text)
    write_note(tmp_path / "VaultA", "ABC101-Alpha/mine.md", text)
    app.reindex()
    hits = app.retrieve("gradient descent optimiser", "ALL", 5, "VaultA")
    assert [h["kind"] for h in hits][:2] == ["human", "raw"]


def test_one_file_cannot_fill_every_slot(tmp_path):
    app = make_app(tmp_path, "solo", ("VaultA",))
    body = "".join(f"## Part {i}\n\nCombustion cycles in an automobile engine, part {i}, with a long enough paragraph to become its own chunk.\n\n" for i in range(8))
    write_note(tmp_path / "VaultA", "ABC101-Alpha/long.md", "# Long\n\n" + body)
    write_note(tmp_path / "VaultA", "ABC101-Alpha/short.md", CAR)
    app.reindex()
    hits = app.retrieve("automobile engine combustion", "ALL", 8, "VaultA")
    assert sum(h["path"].endswith("long.md") for h in hits) <= 3
    assert any(h["path"].endswith("short.md") for h in hits)


def test_empty_index_returns_nothing(tmp_path):
    app = make_app(tmp_path, "solo", ("VaultA",))
    assert app.retrieve("anything at all", "ALL", 5, "VaultA") == []


def test_reindex_notices_edits_and_deletions(tmp_path):
    app = make_app(tmp_path, "solo", ("VaultA",))
    p = write_note(tmp_path / "VaultA", "ABC101-Alpha/a.md", CAR)
    app.reindex()
    assert paths(app.retrieve("combustion", "ALL", 3, "VaultA")) == ["VaultA/ABC101-Alpha/a.md"]
    p.unlink()
    app.reindex()
    assert app.retrieve("combustion", "ALL", 3, "VaultA") == []


@pytest.mark.parametrize("question", ["and why?", "Why does it do that?", "explain more", "what about momentum", "give me an example", "ok", "So how?"])
def test_followups_borrow_the_previous_question(app_solo, question):
    assert app_solo.retrieval_query(question, "How does gradient descent work?") == f"How does gradient descent work?\n{question}"


@pytest.mark.parametrize("question", [
    "Who won the 2018 FIFA World Cup?",                                   # short, but a new topic: must not drag the old notes in
    "How do I make a sourdough starter from scratch?",
    "What happens in gradient descent if the learning rate is too large? Answer with an example and the formula for the update step.",
])
def test_standalone_questions_search_on_their_own(app_solo, question):
    assert app_solo.retrieval_query(question, "How does gradient descent work?") == question


def test_no_previous_question_means_no_borrowing(app_solo):
    assert app_solo.retrieval_query("and why?", "") == "and why?"
