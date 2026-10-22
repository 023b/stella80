"""The vault agent only keeps a concept if the model's supporting quote really is in the text it was reading.
This is the guard against invented concepts, so it is tested both directly and through process_doc."""
import json

import pytest

SOURCE = ("Gradient descent minimises a loss by stepping against the gradient. The learning rate sets the step size; "
          "if it is too large the loss oscillates or diverges. Momentum keeps a running average of past gradients. ") * 3


@pytest.mark.parametrize("quote", [
    "The learning rate sets the step size",
    "the LEARNING RATE sets the step size!",                       # case and punctuation do not matter
    "if it is too large the loss oscillates or diverges",
    "Momentum keeps a running average of past gradients",
])
def test_real_quotes_are_accepted(agent, quote):
    assert agent.quote_ok(quote, SOURCE)


@pytest.mark.parametrize("quote", [
    "Adam combines momentum with per-parameter scaling",           # a true fact that the text never says
    "The learning rate controls how fast the batch size grows",    # reuses words, changes the meaning
    "a very small step",                                           # too short to prove anything
    "",
])
def test_invented_quotes_are_rejected(agent, quote):
    assert not agent.quote_ok(quote, SOURCE)


def make_doc(agent, tmp_path, text):
    src = tmp_path / "agent" / "src" / "Machine-Learning"
    src.mkdir(parents=True)
    p = src / "lecture.md"
    p.write_text("# Lecture\n\n" + text)
    c = agent.db()
    c.execute("INSERT INTO docs(id,path,sha,kind,course,title,status,doctype,link) VALUES(1,?,?,?,?,?,?,?,?)",
              (str(p), "x", "doc", json.dumps(["Machine-Learning", "00-Machine-Learning", "machine-learning"]), "Lecture", "pending", "md", ""))
    c.commit()
    return c, p


def test_process_doc_drops_concepts_without_a_matching_quote(agent, tmp_path):
    text = ("The learning rate is the single most important hyperparameter of gradient descent. If it is too large each step overshoots "
            "the minimum and the loss oscillates or diverges. Momentum keeps a running average of past gradients and damps oscillation "
            "across narrow valleys. ") * 2
    c, p = make_doc(agent, tmp_path, text)
    answer = {"summary": "optimisers", "concepts": [
        {"name": "Learning rate", "kind": "concept", "definition": "step size", "quote": "If it is too large each step overshoots the minimum"},
        {"name": "Momentum", "kind": "method", "definition": "running average", "quote": "Momentum keeps a running average of past gradients"},
        {"name": "Adam optimiser", "kind": "algorithm", "definition": "adaptive steps", "quote": "Adam rescales each parameter by its second moment"},
        {"name": "Batch normalisation", "kind": "method", "definition": "normalises activations", "quote": "short quote"},
    ]}
    calls = []
    agent.chat = lambda messages, schema, **kw: calls.append(messages) or answer
    agent.process_doc(c, 1, p, "doc", ["Machine-Learning", "00-Machine-Learning", "machine-learning"])
    names = {r[0] for r in c.execute("SELECT name FROM concepts")}
    assert names == {"Learning rate", "Momentum"}
    assert c.execute("SELECT COUNT(*) FROM mentions").fetchone()[0] == 2
    assert c.execute("SELECT status FROM docs WHERE id=1").fetchone()[0] == "done"
    assert len(calls) == 1


def test_a_model_that_invents_everything_adds_nothing(agent, tmp_path):
    c, p = make_doc(agent, tmp_path, "Plain notes about cooking pasta and sauces, nothing technical here at all. " * 6)
    agent.chat = lambda messages, schema, **kw: {"summary": "x", "concepts": [
        {"name": "Transformer", "kind": "model", "definition": "attention", "quote": "Self attention relates every token to every other token"}]}
    agent.process_doc(c, 1, p, "doc", ["Machine-Learning", "00-Machine-Learning", "machine-learning"])
    assert c.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0
    assert c.execute("SELECT status FROM docs WHERE id=1").fetchone()[0] == "done"


def test_unparseable_model_output_stops_the_run_instead_of_losing_the_chunk(agent, tmp_path):
    c, p = make_doc(agent, tmp_path, "Gradient descent steps against the gradient of the loss by a learning rate. " * 8)
    agent.chat = lambda messages, schema, **kw: None   # what chat() returns after three bad answers
    with pytest.raises(agent.Offline):
        agent.process_doc(c, 1, p, "doc", ["Machine-Learning", "00-Machine-Learning", "machine-learning"])
    assert c.execute("SELECT status FROM docs WHERE id=1").fetchone()[0] != "done"
