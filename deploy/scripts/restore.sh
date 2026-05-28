#!/usr/bin/env bash
# Restore a Synapse vault from a backup tarball.
#
# Usage:
#   sudo bash deploy/scripts/restore.sh /var/backups/synapse/synapse-20260528T120000Z.tar.gz
#   # ...or from a remote rclone path:
#   sudo bash deploy/scripts/restore.sh spaces:synapse-backups/synapse-20260528T120000Z.tar.gz
#
# What this does:
#   1. Stops synapse-gateway (so nothing is writing to the vault during restore)
#   2. Renames the existing vault to vault-PRERESTORE-<timestamp> (NEVER deletes it)
#   3. Extracts the backup to $SYNAPSE_VAULT_PATH
#   4. chowns to the synapse user
#   5. Restarts the gateway
#
# Per CLAUDE.md: this script does NOT delete the prior vault. You can always
# undo the restore by stopping the gateway, swapping the dirs back, and starting again.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <local-tarball-or-rclone-path>" >&2
  exit 1
fi

SOURCE="$1"
VAULT="${SYNAPSE_VAULT_PATH:-/opt/synapse-vault}"
SVC_USER="${SYNAPSE_SERVICE_USER:-synapse}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: must run as root (use sudo)." >&2
  exit 1
fi

# Fetch via rclone if SOURCE looks like a remote path (contains ':').
if [[ "$SOURCE" == *:* && ! -f "$SOURCE" ]]; then
  echo "▶ fetching from rclone: $SOURCE"
  command -v rclone >/dev/null 2>&1 || { echo "rclone not installed" >&2; exit 1; }
  rclone copy "$SOURCE" "$WORK_DIR/"
  LOCAL_ARCHIVE="$WORK_DIR/$(basename "$SOURCE")"
else
  LOCAL_ARCHIVE="$SOURCE"
fi

[[ -f "$LOCAL_ARCHIVE" ]] || { echo "ERROR: archive not found: $LOCAL_ARCHIVE" >&2; exit 1; }

echo "▶ stopping synapse-gateway"
systemctl stop synapse-gateway || true

if [[ -d "$VAULT" ]]; then
  BACKUP_NAME="${VAULT}-PRERESTORE-${TS}"
  echo "▶ preserving current vault → $BACKUP_NAME"
  mv "$VAULT" "$BACKUP_NAME"
fi

echo "▶ extracting $LOCAL_ARCHIVE → $VAULT"
mkdir -p "$(dirname "$VAULT")"
tar -xzf "$LOCAL_ARCHIVE" -C "$(dirname "$VAULT")"

echo "▶ chown to $SVC_USER"
chown -R "$SVC_USER:$SVC_USER" "$VAULT"

echo "▶ starting synapse-gateway"
systemctl start synapse-gateway

cat <<EOF
✓ restore complete

The previous vault (if any) was preserved as ${VAULT}-PRERESTORE-${TS}.
Verify the restore looks right, then delete the preserved copy manually:
  sudo rm -rf ${VAULT}-PRERESTORE-${TS}
EOF
