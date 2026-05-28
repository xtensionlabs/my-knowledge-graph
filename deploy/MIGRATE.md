# Migrating Synapse from Windows to the VPS

You've been running Synapse locally on Windows. This guide moves the vault + DB to the VPS, then points your local clients (clipboard daemon, browser extension, dashboard) at the new gateway URL.

The migration is **one-way and one-time**. After it completes, the VPS is the source of truth.

## Before you start

The VPS must already be set up via `deploy/scripts/setup.sh` and the gateway must be reachable at a Cloudflare Tunnel URL. See [INSTALL.md](INSTALL.md).

You need:
- Your Cloudflare Tunnel URL (e.g., `https://small-river-2718.trycloudflare.com`)
- SSH access to the VPS as a user with `sudo`
- The current location of your Windows vault (default: `D:\DEV\my-knowledge-graph\vault`)

## Step 1 — stop the local gateway

On Windows, in the terminal running `uv run synapse start`:

```
Ctrl+C
```

Also stop the clipboard daemon if it's running:
```powershell
uv run synapse daemon stop
```

This guarantees nothing writes to the local vault during the rsync.

## Step 2 — rsync the vault to the VPS

From a Windows PowerShell or Git Bash (Git Bash gives you rsync; PowerShell needs WSL):

```bash
# Git Bash:
rsync -avhP --exclude='.synapse/run' --exclude='.synapse/logs' \
  /d/DEV/my-knowledge-graph/vault/ \
  synapse@<VPS_IP>:/opt/synapse-vault/
```

If rsync isn't available, `scp -r` works but is slower:
```bash
scp -r /d/DEV/my-knowledge-graph/vault/* synapse@<VPS_IP>:/opt/synapse-vault/
```

What gets copied:
- `inbox/`, `archive/`, `concepts/`, `builds/`, `events/`, `questions/`, `insights/`, `daily/`, `strategy/`, `scout/`, `attachments/`, `xtension/`, `courses/`
- `.synapse/synapse.db` (the SQLite knowledge graph)
- `.synapse/chroma/` (the vector store)
- `pending_review.md`, `pending_insights.md`

What gets excluded:
- `.synapse/run/` (PID files — VPS will write its own)
- `.synapse/logs/` (Windows-side logs — VPS uses journald)

## Step 3 — fix permissions on the VPS

```bash
ssh root@<VPS_IP>
chown -R synapse:synapse /opt/synapse-vault
exit
```

## Step 4 — start the VPS gateway

```bash
ssh root@<VPS_IP>
systemctl start synapse-gateway
systemctl status synapse-gateway
journalctl -u synapse-gateway -f
```

You should see:
- `database initialized at /opt/synapse-vault/.synapse/synapse.db`
- `gateway up (v...) on 127.0.0.1:8000`
- `scheduler started (Africa/Nairobi, 8 jobs)`

If alembic has any new migrations since the migration was done, they'll auto-apply at startup.

## Step 5 — verify via the public URL

From your laptop:

```bash
curl https://<your-tunnel-url>/health
```

Should print `{"status": "ok", "version": "...", ...}` with `vault_initialized: true` and a non-zero `inbox_count` if you had unprocessed items.

Also hit the dashboard endpoint to test auth:
```bash
curl -H "x-synapse-api-key: <YOUR_KEY>" https://<your-tunnel-url>/dashboard/overview
```

Should show your real node counts.

## Step 6 — repoint local clients

### Browser extension
- Click the extension icon → gear → Options
- Change "Gateway URL" from `http://127.0.0.1:8000` to your Cloudflare Tunnel URL
- Click "Test connection" — should report the graph node count

### Dashboard (if you want it to read remote data)
Edit `dashboard/.env.local`:
```
SYNAPSE_GATEWAY_URL=https://<your-tunnel-url>
SYNAPSE_API_KEY=<same-as-VPS-SYNAPSE_BROWSER_API_KEY>
```
Restart `npm run dev`.

### Clipboard daemon (local-only, points to remote gateway)
The clipboard daemon reads `SYNAPSE_GATEWAY_HOST` + `SYNAPSE_GATEWAY_PORT` from local `.env`. Either:
- Edit local `.env` to point at the remote URL (requires teaching the daemon to use HTTPS — not yet supported), OR
- **Recommended:** keep the clipboard daemon on the laptop pointing at a local lightweight forwarder, OR
- **Simpler for M6 v1:** stop the clipboard daemon. Use the browser extension + Telegram bot for capture from anywhere.

### Telegram bot
The bot now runs on the VPS, not the laptop. Make sure `TELEGRAM_BOT_TOKEN` is in `/opt/synapse/.env` on the VPS. Stop sending captures to a local instance.

### Email webhook
Update your Cloudflare Email Routing rule to forward to `https://<your-tunnel-url>/ingest/email` (with the correct HMAC secret).

## Step 7 — archive (don't delete) the Windows vault

Even after a successful migration, keep the Windows vault around for at least a week:

```powershell
Rename-Item D:\DEV\my-knowledge-graph\vault D:\DEV\my-knowledge-graph\vault-pre-vps-migration
```

You can delete it once the VPS has run a successful backup cycle.

## Rollback plan

If something goes catastrophically wrong:

1. On the VPS, stop the gateway: `systemctl stop synapse-gateway`
2. On Windows, rename `vault-pre-vps-migration` back to `vault`
3. Repoint the extension/dashboard back to `http://127.0.0.1:8000`
4. Start the local gateway: `uv run synapse start`

You've lost nothing.

## Quick checklist

- [ ] Local gateway stopped
- [ ] Vault rsynced to `/opt/synapse-vault` on VPS
- [ ] `chown -R synapse:synapse /opt/synapse-vault`
- [ ] `systemctl start synapse-gateway` succeeds
- [ ] `curl <tunnel>/health` returns 200 with `status: ok`
- [ ] Browser extension test-connection works
- [ ] First Telegram capture round-trips through the VPS
- [ ] First nightly backup succeeds (`systemctl list-timers synapse-backup.timer`)
- [ ] Windows vault archived (not deleted) for one week
