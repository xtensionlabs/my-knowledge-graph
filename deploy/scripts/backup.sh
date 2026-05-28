#!/usr/bin/env bash
# Synapse nightly backup — vault + DB tarball, local snapshot, rclone push to DO Spaces.
#
# What gets backed up:
#   - $SYNAPSE_VAULT_PATH (the user's knowledge graph: inbox, archive, concepts,
#     builds, daily briefs, etc.)
#   - $SYNAPSE_VAULT_PATH/.synapse/synapse.db (SQLite — the graph source of truth)
#   - $SYNAPSE_VAULT_PATH/.synapse/chroma/ (vector store — re-buildable but slow)
#
# What does NOT get backed up:
#   - The repo at /opt/synapse (re-clonable via git)
#   - Logs (re-generatable)
#
# Restore: `bash restore.sh <snapshot.tar.gz>`
#
# Cron / systemd-timer invocation: see deploy/systemd/synapse-backup.timer

set -euo pipefail

VAULT="${SYNAPSE_VAULT_PATH:-/opt/synapse-vault}"
LOCAL_DIR="${SYNAPSE_BACKUP_DIR:-/var/backups/synapse}"
REMOTE="${SYNAPSE_BACKUP_REMOTE:-spaces:synapse-backups}"   # rclone remote
RETAIN_DAYS="${SYNAPSE_BACKUP_RETAIN_DAYS:-30}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$LOCAL_DIR/synapse-$TS.tar.gz"

mkdir -p "$LOCAL_DIR"

if [[ ! -d "$VAULT" ]]; then
  echo "ERROR: vault not found at $VAULT" >&2
  exit 1
fi

echo "▶ snapshotting $VAULT → $ARCHIVE"
# `--checkpoint` gives periodic progress; `--warning=no-file-changed` so the
# SQLite WAL file rolling underneath us isn't treated as a fatal error.
tar \
  --warning=no-file-changed \
  --exclude='*.db-wal' --exclude='*.db-shm' \
  -czf "$ARCHIVE" \
  -C "$(dirname "$VAULT")" \
  "$(basename "$VAULT")"

SIZE=$(stat -c %s "$ARCHIVE" 2>/dev/null || stat -f %z "$ARCHIVE")
echo "  → $(numfmt --to=iec "$SIZE" 2>/dev/null || echo "${SIZE}B")"

# Push to remote (optional — set SYNAPSE_BACKUP_REMOTE='' to skip).
if [[ -n "$REMOTE" ]] && command -v rclone >/dev/null 2>&1; then
  echo "▶ rclone copy → $REMOTE"
  rclone copy "$ARCHIVE" "$REMOTE/" --progress
else
  echo "  (skipping remote push — REMOTE='$REMOTE' or rclone missing)"
fi

# Prune local snapshots older than RETAIN_DAYS.
echo "▶ pruning local snapshots older than ${RETAIN_DAYS}d"
find "$LOCAL_DIR" -name 'synapse-*.tar.gz' -mtime "+$RETAIN_DAYS" -delete -print

# Prune remote snapshots — rclone handles it with --min-age.
if [[ -n "$REMOTE" ]] && command -v rclone >/dev/null 2>&1; then
  echo "▶ pruning remote snapshots older than ${RETAIN_DAYS}d"
  rclone delete "$REMOTE/" --min-age "${RETAIN_DAYS}d" --include 'synapse-*.tar.gz'
fi

echo "✓ backup complete"
