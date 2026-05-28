# Deploying Synapse to a VPS

This is the "from zero to always-on" walkthrough. End state: Synapse runs on a $12/mo DigitalOcean Droplet (covered by your GitHub Student credit), reachable at a `*.trycloudflare.com` URL from anywhere — phone, laptop, browser extension, Cloudflare email routing.

**Time to complete:** ~30 minutes of active work.

## Prerequisites

- GitHub Student Developer Pack (free, ~5 min to apply at [education.github.com/pack](https://education.github.com/pack))
- A free Cloudflare account (no domain required)
- SSH client (Windows: built-in `ssh` works, or Git Bash, or WSL)
- A clean `.env` ready to paste with your API keys (Anthropic, Telegram bot token, etc.)

## Step 1 — Claim DigitalOcean credit

1. Go to [digitalocean.com/github-students](https://www.digitalocean.com/github-students)
2. Click "Get free credit", sign in with GitHub
3. Verify your GitHub Student status — you should land on a screen confirming **$200 credit / 12 months**
4. Add a credit card (required for verification; it won't be charged unless you spend over $200 or after the 12 months)

## Step 2 — Create the Droplet

In the DO control panel → "Create" → "Droplets":

| Setting | Value | Why |
|---|---|---|
| Region | **Bangalore (BLR1)** | Lowest latency from Nairobi (~100ms vs ~150ms for European regions) |
| Image | Ubuntu 24.04 (LTS) x64 | Stable; what the systemd unit + setup.sh target |
| Size | Basic / Premium AMD / **2 GB / 1 vCPU / 50 GB SSD ($12/mo)** | Comfortable for Synapse + Chroma + ~20-month credit runway |
| Authentication | **SSH key** (paste your `~/.ssh/id_ed25519.pub`) | Password auth invites brute force; never use it |
| Hostname | `synapse-prod` | Any name; just for your reference |
| Backups | **Off** (we run our own to Spaces) | DO backups cost +20%; we'll snapshot ourselves |

Click "Create Droplet". ~90 seconds later you'll have an IP address.

## Step 3 — First SSH

```bash
ssh root@<DROPLET_IP>
```

If you're on Windows without SSH keys configured, generate them first:
```powershell
ssh-keygen -t ed25519
# accept defaults; no passphrase if you want unattended ops
```
Then copy `~/.ssh/id_ed25519.pub` content into the DO Droplet creation form (you can rebuild the droplet if you forgot).

## Step 4 — Run the provisioning script

Still SSHed into the Droplet as root:

```bash
curl -fsSL https://raw.githubusercontent.com/xtensionlabs/my-knowledge-graph/main/deploy/scripts/setup.sh | bash
```

What it does (~3 minutes):
- Installs apt deps (git, build-essential, tesseract, sqlite3, rsync, ufw)
- Creates a `synapse` system user with home `/home/synapse`
- Installs `uv` (Python dep manager)
- Clones the repo to `/opt/synapse`
- `uv sync` (Python deps + alembic)
- Writes a `.env` scaffold at `/opt/synapse/.env`
- Provisions `/var/log/synapse` and `/opt/synapse-vault`
- Links the systemd unit at `/etc/systemd/system/synapse-gateway.service`

At the end it prints "Setup complete" with next-step commands.

## Step 5 — Fill in `.env`

```bash
nano /opt/synapse/.env
```

Required:
- `SYNAPSE_SECRET_KEY` — generate via:
  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- `ANTHROPIC_API_KEY` — from console.anthropic.com (must have credit balance)
- `SYNAPSE_BROWSER_API_KEY` — any long random string; must match `dashboard/.env.local` later

Recommended:
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_USER_ID`
- `SYNAPSE_EMAIL_WEBHOOK_SECRET` (if using Cloudflare Email Routing)

`Ctrl+O`, `Enter`, `Ctrl+X` to save.

## Step 6 — Initialize the vault + start the service

```bash
sudo -u synapse -H bash -lc "cd /opt/synapse && /home/synapse/.local/bin/uv run synapse init"
systemctl enable --now synapse-gateway
journalctl -u synapse-gateway -f
```

You should see (within ~10 seconds):
```
synapse.graph.db: database initialized at /opt/synapse-vault/.synapse/synapse.db
synapse.gateway.main: gateway up (v...) on 127.0.0.1:8000
synapse.scheduler: scheduler started (Africa/Nairobi, 8 jobs)
```

`Ctrl+C` to exit the log tail (the service keeps running).

Sanity check:
```bash
curl http://127.0.0.1:8000/health
```
Should print JSON with `"status": "ok"`.

## Step 7 — Set up Cloudflare Tunnel

See [cloudflared/README.md](cloudflared/README.md). Quick path:

```bash
# Install
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared jammy main" | tee /etc/apt/sources.list.d/cloudflared.list
apt-get update -qq && apt-get install -y cloudflared

# Quickest: random URL, no domain, no Cloudflare account needed
cloudflared tunnel --url http://127.0.0.1:8000
```

The output includes a line like:
```
https://random-name-1234.trycloudflare.com
```

That's your public Synapse URL. Test it from anywhere:
```bash
curl https://random-name-1234.trycloudflare.com/health
```

For a stable URL, follow Path B in `cloudflared/README.md`.

## Step 8 — Enable nightly backups

Edit `/opt/synapse/.env` and add the rclone remote name:
```
SYNAPSE_BACKUP_REMOTE=spaces:synapse-backups
```

Configure rclone with DO Spaces (one-time, interactive):
```bash
sudo -u synapse rclone config
# choose: n → name=spaces → storage=s3 → provider=DigitalOcean
# endpoint=<region>.digitaloceanspaces.com (e.g., blr1.digitaloceanspaces.com)
# access key + secret from DO control panel → Spaces → Access Keys
```

Create the Space in the DO control panel (free tier: NA but $5/mo for 250GB — well within your credit).

Then enable the timer:
```bash
cp /opt/synapse/deploy/systemd/synapse-backup.service /etc/systemd/system/
cp /opt/synapse/deploy/systemd/synapse-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now synapse-backup.timer
systemctl list-timers synapse-backup.timer
```

Run one backup immediately to verify:
```bash
systemctl start synapse-backup.service
journalctl -u synapse-backup.service --since "1 min ago"
ls -lh /var/backups/synapse/
rclone ls spaces:synapse-backups/
```

## Step 9 — (Optional) free external monitoring

Sign up at [uptimerobot.com](https://uptimerobot.com) (free: 50 monitors). Add an HTTP(s) monitor:
- URL: `https://<your-tunnel-url>/health`
- Type: HTTP(s)
- Interval: 5 minutes

You'll get an email if `/health` ever returns non-200 (we made it return 503 when degraded).

## Step 10 — Migrate your existing vault

See [MIGRATE.md](MIGRATE.md) — rsync your Windows vault to `/opt/synapse-vault`, then repoint your browser extension + dashboard at the new URL.

## Day-2 operations

| Task | Command |
|---|---|
| Tail logs | `journalctl -u synapse-gateway -f` |
| Restart Synapse | `systemctl restart synapse-gateway` |
| Pull new code | `sudo -u synapse git -C /opt/synapse pull && sudo -u synapse /home/synapse/.local/bin/uv sync --cwd /opt/synapse && systemctl restart synapse-gateway` |
| Manual backup | `systemctl start synapse-backup.service` |
| Restore from backup | `sudo bash /opt/synapse/deploy/scripts/restore.sh /var/backups/synapse/synapse-<TS>.tar.gz` |
| List timers | `systemctl list-timers` |
| Check disk usage | `df -h /opt` and `du -sh /opt/synapse-vault` |

## Cost reality check

| Item | Monthly cost | Covered by GitHub credit? |
|---|---|---|
| 2GB Droplet | $12 | ✅ ($200 / 12mo = $16.67 budget) |
| DO Space (250GB, for backups) | $5 | ✅ |
| Cloudflare Tunnel | $0 | n/a |
| UptimeRobot | $0 | n/a |
| **Total** | **$17** | ~10 months free, then $17/mo (and the credit renews annually while you're a student) |
