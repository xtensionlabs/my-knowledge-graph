#!/usr/bin/env bash
# Redeploy Synapse on the VPS after a `git push` from your laptop.
#
# Usage (as root on the VPS):
#   bash /opt/synapse/deploy/scripts/redeploy.sh                # update everything
#   bash /opt/synapse/deploy/scripts/redeploy.sh gateway        # backend only (faster)
#   bash /opt/synapse/deploy/scripts/redeploy.sh dashboard      # frontend only
#   bash /opt/synapse/deploy/scripts/redeploy.sh extension      # no service to restart — just `git pull`
#
# What it does (full mode):
#   1. git pull (fast-forward only — bails if you've made local edits on the VPS)
#   2. If backend files changed → uv sync + restart synapse-gateway
#      (alembic migrations apply automatically on gateway startup)
#   3. If dashboard files changed → npm install + npm run build + restart synapse-dashboard
#   4. Print final health snapshot
#
# What it does NOT do:
#   - touch .env (your secrets stay where they are)
#   - re-run alembic by hand (gateway startup handles it)
#   - rebuild things that didn't change (incremental, fast)

set -euo pipefail

MODE="${1:-all}"
REPO_DIR="/opt/synapse"
SVC_USER="synapse"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: run as root (sudo)." >&2
  exit 1
fi

step() { printf "\n\033[1;35m▶ %s\033[0m\n" "$*"; }
note() { printf "  \033[2m%s\033[0m\n" "$*"; }

# ── 1. git pull ──────────────────────────────────────────────────────────────
step "git pull"
PREV_HEAD=$(sudo -u "$SVC_USER" git -C "$REPO_DIR" rev-parse HEAD)
sudo -u "$SVC_USER" git -C "$REPO_DIR" fetch --quiet origin
sudo -u "$SVC_USER" git -C "$REPO_DIR" pull --ff-only
NEW_HEAD=$(sudo -u "$SVC_USER" git -C "$REPO_DIR" rev-parse HEAD)

if [[ "$PREV_HEAD" == "$NEW_HEAD" ]]; then
  note "no new commits — nothing changed"
  CHANGED=""
else
  note "$PREV_HEAD → $NEW_HEAD"
  # Build a list of changed paths so we only rebuild what actually changed.
  CHANGED=$(sudo -u "$SVC_USER" git -C "$REPO_DIR" diff --name-only "$PREV_HEAD" "$NEW_HEAD")
fi

backend_changed() {
  [[ "$MODE" == "all" || "$MODE" == "gateway" ]] && \
    { [[ -z "$CHANGED" && "$MODE" == "gateway" ]] || echo "$CHANGED" | grep -qE '^(synapse/|alembic/|pyproject\.toml|uv\.lock)' ; }
}

dashboard_changed() {
  [[ "$MODE" == "all" || "$MODE" == "dashboard" ]] && \
    { [[ -z "$CHANGED" && "$MODE" == "dashboard" ]] || echo "$CHANGED" | grep -qE '^dashboard/' ; }
}

# ── 2. backend ───────────────────────────────────────────────────────────────
if backend_changed; then
  step "uv sync"
  sudo -u "$SVC_USER" -H bash -lc "cd $REPO_DIR && /home/$SVC_USER/.local/bin/uv sync"
  step "restart synapse-gateway"
  systemctl restart synapse-gateway
  note "alembic migrations (if any) applied during startup"
else
  note "skipped backend — no relevant changes"
fi

# ── 3. dashboard ─────────────────────────────────────────────────────────────
if dashboard_changed; then
  step "sync dashboard .env.local with gateway .env"
  # Auto-mirror SYNAPSE_BROWSER_API_KEY → SYNAPSE_API_KEY so the dashboard's
  # server-side fetch never 401s after a key rotation. SYNAPSE_GATEWAY_URL
  # stays at localhost since both services run on the same machine.
  ENVL="$REPO_DIR/dashboard/.env.local"
  KEY=$(grep '^SYNAPSE_BROWSER_API_KEY=' "$REPO_DIR/.env" | cut -d= -f2- || true)
  if [[ -z "$KEY" ]]; then
    echo "  ⚠ SYNAPSE_BROWSER_API_KEY missing from $REPO_DIR/.env — dashboard will 401"
  fi
  cat > "$ENVL" <<EOF
SYNAPSE_GATEWAY_URL=http://127.0.0.1:8000
SYNAPSE_API_KEY=$KEY
EOF
  chown "$SVC_USER:$SVC_USER" "$ENVL"
  chmod 600 "$ENVL"

  step "npm install + build"
  sudo -u "$SVC_USER" -H bash -lc "
    cd $REPO_DIR/dashboard
    npm install
    npm run build
  "
  step "restart synapse-dashboard"
  systemctl restart synapse-dashboard
else
  note "skipped dashboard — no relevant changes"
fi

# ── 4. extension ─────────────────────────────────────────────────────────────
if [[ "$MODE" == "extension" ]] || echo "$CHANGED" | grep -qE '^extension/'; then
  note "extension files updated in the repo — reload the unpacked extension at chrome://extensions/"
fi

# ── 5. health snapshot ───────────────────────────────────────────────────────
step "post-deploy health"
sleep 2
curl -fsS http://127.0.0.1:8000/health \
  | python3 -m json.tool 2>/dev/null \
  || { echo "  ⚠ gateway not responding"; exit 1; }

if systemctl is-active --quiet synapse-dashboard; then
  if curl -fsS -o /dev/null -w "  dashboard HTTP %{http_code}\n" http://127.0.0.1:3000; then
    :
  fi
fi

echo
echo "✓ redeploy complete"
