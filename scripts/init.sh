#!/usr/bin/env bash
# Prepare a fresh checkout: config files, the default vault folders, and optionally a sample lecture note.
#   scripts/init.sh solo            one person, this machine only
#   scripts/init.sh shared          home server for you and friends
#   add --sample to copy a small example lecture into the source folder
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-solo}"
[[ "$mode" == solo || "$mode" == shared ]] || { echo "usage: scripts/init.sh solo|shared [--sample]"; exit 1; }

if [[ ! -f stella80.yaml ]]; then
  sed "s/^mode: solo$/mode: $mode/" stella80.example.yaml > stella80.yaml
  echo "wrote stella80.yaml (mode: $mode)"
else
  echo "stella80.yaml exists, left alone"
fi
[[ -f .env ]] || cp .env.example .env

mkdir -p data vaults/main/src vaults/main/vault/Study-Vault vaults/main/state
[[ -f vaults/main/agent.yaml ]] || cp agent/config.example.yaml vaults/main/agent.yaml

if [[ "${2:-}" == "--sample" ]]; then
  cp -Rn examples/sample-src/. vaults/main/src/ 2>/dev/null || true
  # the agent leaves files younger than a minute alone (they may still be syncing), so date the samples back
  past=$(( $(date +%s) - 120 ))
  find vaults/main/src -type f -name '*.md' -exec touch -d "@$past" {} + 2>/dev/null ||
    find vaults/main/src -type f -name '*.md' -exec touch -t "$(date -r "$past" +%Y%m%d%H%M)" {} +   # macOS
  echo "copied the sample lecture into vaults/main/src"
fi

cat <<MSG

Ready. Put lecture files in vaults/main/src/<Subject>/ and open vaults/main/vault in Obsidian.
Next: docs/INSTALL.md (Docker or bare metal).
MSG
[[ "$mode" == shared ]] && cat <<MSG
Shared mode: after starting it, create the first account with
  docker compose exec web python stella80/manage.py add <name> --admin     (Docker)
  .venv/bin/python stella80/manage.py add <name> --admin                   (bare metal)
MSG
true
