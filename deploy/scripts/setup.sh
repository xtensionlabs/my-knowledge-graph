#!/usr/bin/env bash
# Synapse VPS bootstrap — idempotent, safe to re-run.
#
# Usage (as root on a fresh Ubuntu 22.04 / 24.04 droplet):
#   curl -fsSL https://raw.githubusercontent.com/xtensionlabs/my-knowledge-graph/main/deploy/scripts/setup.sh | bash
#   # ...or, if you've already cloned the repo:
#   sudo bash deploy/scripts/setup.sh
#
# What this does:
#   1. Creates a `synapse` system user with home /home/synapse
#   2. Installs apt deps (git, build-essential, tesseract, sqlite3, rsync)
#   3. Installs `uv` for the synapse user
#   4. Clones the repo to /opt/synapse (or pulls if already there)
#   5. `uv sync` to install Python deps + alembic
#   6. Creates /opt/synapse/.env from .env.example if missing (must edit before start)
#   7. Provisions /var/log/synapse for journald-adjacent logging
#   8. Links the systemd unit (does NOT start the service — you do that after .env is filled in)
#
# What this does NOT do:
#   - Set up Cloudflare Tunnel (separate step, requires interactive auth)
#   - Restore a vault from backup (separate `restore.sh`)

set -euo pipefail

REPO_URL="${SYNAPSE_REPO_URL:-https://github.com/xtensionlabs/my-knowledge-graph.git}"
INSTALL_DIR="/opt/synapse"
LOG_DIR="/var/log/synapse"
SVC_USER="synapse"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: this script must run as root (use sudo)." >&2
  exit 1
fi

step() { printf "\n\033[1;35m▶ %s\033[0m\n" "$*"; }

step "1/8  apt update + base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  git curl ca-certificates \
  build-essential pkg-config \
  python3 python3-venv \
  sqlite3 \
  tesseract-ocr \
  rsync \
  ufw

step "2/8  create '$SVC_USER' system user"
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash "$SVC_USER"
fi

step "3/8  install uv for $SVC_USER"
sudo -u "$SVC_USER" -H bash -c '
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
'

step "4/8  clone or pull repo to $INSTALL_DIR"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi
chown -R "$SVC_USER:$SVC_USER" "$INSTALL_DIR"

step "5/8  uv sync (Python deps)"
sudo -u "$SVC_USER" -H bash -lc "cd $INSTALL_DIR && /home/$SVC_USER/.local/bin/uv sync"

step "6/8  .env scaffold"
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  cat > "$INSTALL_DIR/.env" <<'EOF'
# ── Synapse production .env ─────────────────────────────────────────────────
# Fill these in BEFORE starting the systemd service.

# Vault on the VPS lives outside the repo dir so `git pull` never disturbs it.
SYNAPSE_VAULT_PATH=/opt/synapse-vault

# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SYNAPSE_SECRET_KEY=

SYNAPSE_GATEWAY_HOST=127.0.0.1
SYNAPSE_GATEWAY_PORT=8000
SYNAPSE_LOG_LEVEL=INFO

# Claude — required from M1 onward
ANTHROPIC_API_KEY=

# Telegram (optional but recommended on VPS)
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_ID=

# Email webhook (Cloudflare Email Routing → /ingest/email)
SYNAPSE_EMAIL_WEBHOOK_SECRET=

# Browser extension + dashboard auth (must match dashboard/.env.local)
SYNAPSE_BROWSER_API_KEY=

# Google Calendar OAuth (optional)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
EOF
  chown "$SVC_USER:$SVC_USER" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
  echo "  → wrote $INSTALL_DIR/.env (fill in the blanks!)"
else
  echo "  → $INSTALL_DIR/.env already exists; leaving it untouched"
fi

step "7/8  log dir + vault dir"
mkdir -p "$LOG_DIR" /opt/synapse-vault
chown -R "$SVC_USER:$SVC_USER" "$LOG_DIR" /opt/synapse-vault

step "8/8  install systemd unit"
cp "$INSTALL_DIR/deploy/systemd/synapse-gateway.service" /etc/systemd/system/
systemctl daemon-reload

cat <<EOF

────────────────────────────────────────────────────────────────────
✓  Setup complete.

Next steps:
  1. Fill in $INSTALL_DIR/.env (especially ANTHROPIC_API_KEY,
     SYNAPSE_SECRET_KEY, SYNAPSE_BROWSER_API_KEY).
  2. Initialize the vault:
       sudo -u $SVC_USER -H bash -lc "cd $INSTALL_DIR && /home/$SVC_USER/.local/bin/uv run synapse init"
  3. Enable + start the service:
       systemctl enable --now synapse-gateway
       journalctl -u synapse-gateway -f
  4. Install Cloudflare Tunnel: see deploy/cloudflared/README.md
────────────────────────────────────────────────────────────────────
EOF
