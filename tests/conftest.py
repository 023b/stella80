"""Shared test helpers. The app and the agent read their configuration when they are imported, so each test module gets a
private copy loaded against a temporary config. Everything is offline: the embedding model and the chat model are replaced
by small deterministic stand-ins."""
import hashlib
import importlib.util
import itertools
import os
import re
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
_counter = itertools.count()

# Words in the same group get the same direction, so "car" and "automobile" are neighbours the way a real embedding model
# would place them. Everything else is hashed into a fixed slot. The groups are what let a test tell keyword search apart
# from embedding search.
GROUPS = [{"car", "automobile", "vehicle", "motorcar"}, {"optimiser", "optimizer", "minimiser", "descent"}, {"cat", "feline", "kitten"}]
DIM = 48


def fake_vectors(texts):
    out = np.zeros((len(texts), DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            g = next((n for n, grp in enumerate(GROUPS) if w in grp), None)
            slot = g if g is not None else len(GROUPS) + int(hashlib.md5(w.encode()).hexdigest(), 16) % (DIM - len(GROUPS))
            out[i, slot] += 1.0
    return out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-9)


def load_module(path, env):
    """Import a source file as a fresh module with the given environment variables set while it loads."""
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        name = f"_s80_{Path(path).stem}_{next(_counter)}"
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def write_app_config(root, mode, vault_names):
    root.mkdir(parents=True, exist_ok=True)
    vaults = []
    for v in vault_names:
        (root / v).mkdir(exist_ok=True)
        vaults.append(f"  - path: {root / v}\n    title: {v}\n")
    cfg = root / "stella80.yaml"
    cfg.write_text(f"mode: {mode}\nport: 8480\ndata_dir: {root / 'data'}\nvaults:\n" + "".join(vaults))
    return cfg


def make_app(tmp_path, mode="solo", vault_names=("VaultA",)):
    """A loaded stella80/app.py with an empty database and the fake embedder installed."""
    cfg = write_app_config(tmp_path, mode, vault_names)
    mod = load_module(REPO / "stella80" / "app.py", {"STELLA80_CONFIG": str(cfg), "STELLA80_BIND": "127.0.0.1"})
    mod.embed = lambda texts, prefix: fake_vectors(texts)
    mod.init_db()
    return mod


def write_note(vault_dir, rel, text):
    p = Path(vault_dir) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def app_solo(tmp_path):
    return make_app(tmp_path, "solo")


@pytest.fixture
def app_shared(tmp_path):
    return make_app(tmp_path, "shared")


@pytest.fixture
def agent(tmp_path):
    """A loaded agent/vault_agent.py working inside tmp_path, with no model calls and no waiting."""
    root = tmp_path / "agent"
    for d in ("src", "vault", "state"):
        (root / d).mkdir(parents=True)
    cfg = root / "agent.yaml"
    cfg.write_text("src_root: src\nvault_root: vault\nvault_dir: Study-Vault\nstate_dir: state\nchat_model: fake-chat\n"
                   "embed_model: fake-embed\nauto_courses: true\nchunk_chars: 5000\n")
    mod = load_module(REPO / "agent" / "vault_agent.py", {"VAULT_AGENT_CONFIG": str(cfg), "VAULT_AGENT_RULES": str(REPO / "agent" / "rules.yaml")})
    mod.embed = lambda texts: fake_vectors(texts)
    mod.breathe = lambda *a, **k: None
    mod.free_study_models = lambda *a, **k: None
    return mod
