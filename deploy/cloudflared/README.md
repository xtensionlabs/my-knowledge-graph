# Cloudflare Tunnel for Synapse

Routes a public URL to `127.0.0.1:8000` on your VPS without opening any inbound port. Free, no domain required.

## Install (on the VPS, as root)

```bash
# 1. Install cloudflared
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared jammy main" | tee /etc/apt/sources.list.d/cloudflared.list
apt-get update -qq
apt-get install -y cloudflared
```

## Path A — Quick tunnel (no domain, no Cloudflare account)

Best for "I want a public URL right now":

```bash
sudo -u synapse cloudflared tunnel --url http://127.0.0.1:8000
```

Prints something like `https://random-name-1234.trycloudflare.com`. That URL is alive as long as `cloudflared` is running. Use it for your phone, browser extension, email webhook — anywhere you need to reach the gateway.

**Trade-off:** the URL changes every time you restart cloudflared. Fine for testing; awkward for a persistent setup.

To run it as a service so it survives reboots, write the URL into a wrapper systemd unit (see "Persistent quick tunnel" below).

## Path B — Named tunnel (stable URL, free Cloudflare account, no domain needed)

Stable URL that survives restarts:

```bash
# 1. Authenticate (opens a browser link you paste back)
cloudflared tunnel login

# 2. Create a named tunnel
cloudflared tunnel create synapse
#  → prints a TUNNEL_ID and credentials file path

# 3. Copy + edit the config template
cp /opt/synapse/deploy/cloudflared/config.example.yml /etc/cloudflared/config.yml
nano /etc/cloudflared/config.yml
#  → replace TUNNEL_ID, CREDENTIALS_FILE, and HOSTNAME

# 4. Run as a system service
cloudflared service install
systemctl status cloudflared
```

For a free permanent URL without owning a domain: skip the `hostname:` route in the config, and use `cloudflared tunnel route` with the quick-tunnel option above.

## Path C — Custom domain (~$10/yr, optional)

If you later buy a domain and add it to Cloudflare:

```bash
# Route the tunnel to your subdomain
cloudflared tunnel route dns synapse synapse.your-domain.com
```

Then set `hostname: synapse.your-domain.com` in the config and restart `cloudflared`.

## Persistent quick tunnel (Path A made permanent)

If you want Path A's simplicity but auto-restart, drop this systemd unit:

```ini
# /etc/systemd/system/cloudflared-quick.service
[Unit]
Description=Cloudflare Tunnel (quick — random URL)
After=synapse-gateway.service

[Service]
Type=simple
User=synapse
ExecStart=/usr/bin/cloudflared tunnel --url http://127.0.0.1:8000
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

The random URL changes on every restart; check it with `journalctl -u cloudflared-quick | grep trycloudflare`.
