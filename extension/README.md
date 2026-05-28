# Synapse Web Clipper

A minimal Chromium extension that sends selected text, links, or whole pages straight into your Synapse inbox.

Pure HTML/CSS/JS — no bundler, no build step. The whole extension is ~200 lines of code.

## Capabilities

- **Right-click → "Save selection to Synapse"** when text is selected
- **Right-click → "Save link to Synapse"** on any anchor
- **Right-click → "Save this page to Synapse"** on any page
- **Toolbar popup** for free-form captures with current-tab context
- **Desktop notifications** confirm success / surface errors

All captures hit `POST /ingest/browser` on your local gateway, which writes them to `${VAULT}/inbox/` exactly like the Telegram bot or clipboard daemon — the Librarian processes them on its next 2-hour sweep.

## Install

1. Make sure the Synapse gateway is running: `uv run synapse start --no-telegram --no-clipboard`.
2. Open `chrome://extensions/` in Chrome / Edge / Brave.
3. Toggle **Developer mode** on (top right).
4. Click **Load unpacked**, select the `extension/` folder in this repo.
5. Pin the extension to the toolbar (puzzle-piece icon → pin).
6. Right-click the toolbar icon → **Options** (or click the gear in the popup).
7. Enter:
   - **Gateway URL** — default `http://127.0.0.1:8000`
   - **API key** — must match `SYNAPSE_BROWSER_API_KEY` in the project root `.env`
8. Click **Test connection** — should report the current graph node count.

## Architecture

| File | Role |
|---|---|
| `manifest.json` | Manifest V3 — declares permissions + entry points |
| `background.js` | Service worker — registers the 3 context-menu items, dispatches `/ingest/browser` calls, fires notifications |
| `popup.html/js/css` | Toolbar popup — free-form textarea + "save current page" button |
| `options.html/js/css` | Settings page — gateway URL + API key, test-connection button |
| `lib/api.js` | Shared API helpers (used by all three contexts) — single source of truth for fetch + storage |
| `icons/icon-*.png` | 16/48/128 px PNG icons generated via Pillow |

## Permissions explained

- `contextMenus` — register the right-click items
- `storage` — persist gateway URL + API key via `chrome.storage.sync`
- `notifications` — fire success/failure toasts after each save
- `activeTab` — read the current tab's URL + title for context (no host permission needed beyond the action click)
- `host_permissions: 127.0.0.1 / localhost` — talk to the local gateway. No other origin is allowed.

The extension never sees any remote server. Nothing leaves your machine.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Popup says `401 — API key rejected` | Mismatch between extension's API key and `SYNAPSE_BROWSER_API_KEY` in project `.env`. Run `uv run synapse start` to load the latest `.env`. |
| `Failed to fetch` | Gateway isn't running, or you're using `localhost` where the gateway binds to `127.0.0.1` (or vice versa). Match exactly. |
| Right-click menu missing | Reload the extension at `chrome://extensions/`. Service workers occasionally sleep — clicking the toolbar icon wakes them. |
| Notifications don't appear | Allow Chrome notifications in your OS settings. (Windows: Settings → Notifications → Chrome.) |
