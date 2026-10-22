# Installing STELLA-80

Pick a **mode** first:

- **Solo**: only you, on the machine you sit at. No login screen, and it is reachable from that machine only.
- **Shared**: one always-on machine, such as a mini PC, a desktop or a home server, that you and your friends reach
  from your own laptops. Everyone gets a personal passphrase.

Then pick an **install method**: Docker Compose (the easiest, with Ollama included) or bare metal (a Python venv and
systemd, if you already run Ollama).

You need about 10 GB of free disk for models and, for the default models, 16 to 32 GB of RAM. Smaller models work
on 8 GB; see *Hardware* in the README.

---

## A. Solo with Docker

```bash
git clone https://github.com/023b/stella80 && cd stella80
scripts/init.sh solo --sample      # creates stella80.yaml, .env and vaults/main/, plus one sample lecture
docker compose up -d --build
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull qwen3.5:4b            # QUICK, and a light agent model
docker compose exec ollama ollama pull gemma4:26b-a4b-it-qat # SCHOLAR (skip on small machines, see below)
docker compose exec ollama ollama pull qwen3:30b-a3b         # DEEP and the agent's default (skip on small machines)
```

Open **http://127.0.0.1:8480**.

On a smaller machine, set every model in `stella80.yaml` (`engines:`) and in `vaults/main/agent.yaml`
(`chat_model`, `vision_model`) to models you have pulled, then run `docker compose restart`.

If your user id is not 1000 (check with `id -u`), build with
`docker compose build --build-arg UID=$(id -u) --build-arg GID=$(id -g)` so the containers can write to `vaults/`
and `data/`.

**GPU:** uncomment the NVIDIA or AMD block for the `ollama` service in `compose.yaml`. If Ollama is already
installed on the host, delete the `ollama` service, set `OLLAMA_URL=http://host.docker.internal:11434` for `web`
and `agent`, and add `extra_hosts: ["host.docker.internal:host-gateway"]` to both.

## B. Shared with Docker (a mini PC or home server for friends)

Same as A, with these changes:

```bash
scripts/init.sh shared --sample
```

1. In `.env`, set `STELLA80_BIND` to the address your friends will reach. Use the machine's **Tailscale IP**
   (recommended, `tailscale ip -4`), its LAN IP, or `0.0.0.0` for every interface.
2. `docker compose up -d --build`, then pull the models as in A.
3. Create your own admin account. The command prints a random passphrase once.

   ```bash
   docker compose exec web python stella80/manage.py add yourname --admin
   ```

   Log in, then add your friends from **BOARD, ADMIN**: each one gets a passphrase shown once, to send them
   privately. The same tab removes people, gives anyone a new passphrase (which logs them out everywhere), turns
   accounts off and sets per-person limits. `manage.py` does the same from the terminal (`add`, `remove`,
   `passwd`, `lock`, `list`).

4. Friends open `http://<that address>:8480` and pick their name.

**Reaching it from outside your home.** The safest way is [Tailscale](https://tailscale.com): install it on the
server, then share the machine with each friend from the Tailscale admin console (*Share*). They see this one
machine and nothing else on your network. If you need a public URL, put Caddy or a Cloudflare Tunnel in front for
HTTPS, and set `trust_ip_header` in `stella80.yaml` (for example `cf-connecting-ip`) so the login rate limit sees
real addresses. **Never port-forward plain HTTP.**

**Friends on different courses.** Give each group its own vault. Copy `vaults/main` to, say, `vaults/ds`, set
`vault_dir: DS-Vault` in `vaults/ds/agent.yaml`, add the vault to `stella80.yaml`:

```yaml
vaults:
  - agent_config: vaults/main/agent.yaml
    title: Computer vision
  - agent_config: vaults/ds/agent.yaml
    title: Data science
    student: an MSc Data Science student
```

Then add a second agent service in `compose.yaml` (a copy of `agent` with
`VAULT_AGENT_CONFIG=/app/vaults/ds/agent.yaml` and its own log path). Set `shared_llm_lock: ../llm.lock` in both
agent configs so that only one of them loads a big model at a time. Create accounts with `--vault DS-Vault`.
People only ever see their own vault.

**Feeding files in.** Friends can upload from the *VAULT* tab, or you can share each vault's `src/` folder with
them over Syncthing, set as receive-only on the server. If you do, keep `_uploads` out of the share and turn on
`require_marker: true`. That makes the agent refuse to scan if the share ever goes missing, so a broken sync
can't empty the vault.

## C. Solo on bare metal

You need Python 3.11 or newer, `poppler-utils` and `ghostscript` (`sudo apt install poppler-utils ghostscript`,
or `brew install poppler ghostscript`), and [Ollama](https://ollama.com/download).

```bash
git clone https://github.com/023b/stella80 && cd stella80
scripts/init.sh solo --sample
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ollama pull nomic-embed-text && ollama pull qwen3.5:4b      # plus the bigger models if they fit

# try it in the foreground
.venv/bin/python stella80/app.py &
VAULT_AGENT_CONFIG=vaults/main/agent.yaml .venv/bin/python agent/vault_agent.py run
```

Open **http://127.0.0.1:8480**. To keep it running (Linux, systemd):

```bash
scripts/install-systemd.sh    # web app as a user service, agent on a 10-minute timer
```

## D. Shared on bare metal

As C, but run `scripts/init.sh shared`, then:

```bash
.venv/bin/python stella80/manage.py add yourname --admin
```

Then add friends from **BOARD, ADMIN** in the web UI. The app listens on `0.0.0.0` in shared mode. To limit it to one interface, set `host:` in `stella80.yaml`, for
example to your Tailscale IP. The rest of B applies (Tailscale sharing, several vaults, Syncthing).
For several vaults on bare metal, copy `deploy/systemd/stella80-agent.*` to `stella80-agent-ds.*` and point
`VAULT_AGENT_CONFIG` and the log path at the new vault.

---

## Using it

- Put lecture files in `vaults/main/src/<Subject>/`. Each top-level folder becomes a subject; name it like
  `COMP3010-ComputerVision` to show a module code.
- Open the folder **`vaults/main/vault`** in Obsidian (the folder, not `Study-Vault` inside it). Notes you write
  anywhere outside `_auto/` and `_raw/` are yours, are searched too, and are ranked above generated notes.
- The agent runs every 10 minutes. Progress is shown by the status pill and in *BOARD, VAULT*. The admin can
  reorder or exclude files in the processing queue.
- The web app re-reads the vault every 10 minutes, or straight away with **RE-READ VAULT**.

## Switching from solo to shared

Set `mode: shared` and restart. Your solo history belongs to the account `me` (or your `solo_user`). Give it a
passphrase with `manage.py passwd me` to keep using it.

## Updating

```bash
git pull
docker compose up -d --build        # or: .venv/bin/pip install -r requirements.txt && systemctl --user restart stella80
```

Your data lives in `data/` and `vaults/`, which git ignores. Back up those two folders. The vault is also a git
repository with a commit after every agent run.

## Chat bot or script access

`manage.py bot-token` writes a token to `data/bot-token`. Requests to `/api/bot/board`, `/online`, `/users`,
`/queue`, `/system` and `/vault` with the header `X-Bot-Token: <token>` act as the admin. This is useful for a
Discord or Matrix bot on the same machine.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Engine knobs are greyed out | That model has not been pulled, or Ollama is unreachable. Run `ollama list`, then check the URLs in `stella80.yaml` |
| "falls back to QUICK" notices | Not enough free RAM for the chosen model. Use smaller models, or set `ram_guard: false` if Ollama is on another machine |
| Nothing appears in the vault | Check `vaults/main/state/agent.log`. Files newer than one minute are left for the next run |
| Solo mode refuses to start | It only runs on 127.0.0.1. Use shared mode to serve other devices |
| Login screen has no names | No accounts yet. Run `manage.py add NAME --admin` |
