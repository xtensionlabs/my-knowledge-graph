// Shared API helpers used by background.js, popup.js, options.js.
// Single source of truth for /ingest/browser calls + connection tests.

const DEFAULT_GATEWAY = "http://127.0.0.1:8000";
const HEADER = "x-synapse-api-key";

/** Resolve the saved config (gateway URL + API key) from chrome.storage.sync. */
export async function getConfig() {
  const { gatewayUrl, apiKey } = await chrome.storage.sync.get([
    "gatewayUrl",
    "apiKey",
  ]);
  return {
    gatewayUrl: (gatewayUrl || DEFAULT_GATEWAY).replace(/\/+$/, ""),
    apiKey: apiKey || "",
  };
}

/** Persist the gateway URL + API key. */
export async function setConfig({ gatewayUrl, apiKey }) {
  await chrome.storage.sync.set({ gatewayUrl, apiKey });
}

/**
 * POST a capture to /ingest/browser. Throws on non-2xx.
 *
 * payload: { url, title, content }  — exactly the shape gateway expects.
 */
export async function ingestBrowser(payload) {
  const { gatewayUrl, apiKey } = await getConfig();
  if (!apiKey) {
    throw new Error("API key not set — open the extension options first.");
  }
  const res = await fetch(`${gatewayUrl}/ingest/browser`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      [HEADER]: apiKey,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${body.slice(0, 200)}`);
  }
  return res.json();
}

/**
 * Gateway reachability + auth check for the options "Test" button.
 * Uses /dashboard/overview which requires the API key, so we verify
 * BOTH that the gateway is up AND that the key is correct.
 */
export async function testConnection() {
  const { gatewayUrl, apiKey } = await getConfig();
  const res = await fetch(`${gatewayUrl}/dashboard/overview`, {
    headers: apiKey ? { [HEADER]: apiKey } : {},
  });
  if (res.status === 401) throw new Error("401 — API key rejected by gateway");
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}
