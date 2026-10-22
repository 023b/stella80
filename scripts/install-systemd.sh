#!/usr/bin/env bash
# Bare metal: install user services for the web app and a 10-minute timer for the vault agent, pointing at this checkout.
# Needs a venv at .venv (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt).
set -euo pipefail
cd "$(dirname "$0")/.."
here="$(pwd)"
[[ -x .venv/bin/python ]] || { echo "create the venv first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
dest="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$dest"
for f in deploy/systemd/*; do
  sed "s|@DIR@|$here|g" "$f" > "$dest/$(basename "$f")"
  echo "installed $dest/$(basename "$f")"
done
systemctl --user daemon-reload
systemctl --user enable --now stella80.service stella80-agent.timer
echo
echo "Running. Logs: journalctl --user -u stella80 -f   and   tail -f vaults/main/state/agent.log"
echo "To keep it running after you log out (servers): sudo loginctl enable-linger $USER"
