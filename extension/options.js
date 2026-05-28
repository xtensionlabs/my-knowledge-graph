import { getConfig, setConfig, testConnection } from "./lib/api.js";

const $gatewayUrl = document.getElementById("gateway-url");
const $apiKey = document.getElementById("api-key");
const $form = document.getElementById("config");
const $save = document.getElementById("save");
const $test = document.getElementById("test");
const $status = document.getElementById("status");

(async function init() {
  const cfg = await getConfig();
  $gatewayUrl.value = cfg.gatewayUrl;
  $apiKey.value = cfg.apiKey;
})();

$form.addEventListener("submit", async (e) => {
  e.preventDefault();
  $save.disabled = true;
  try {
    await setConfig({
      gatewayUrl: $gatewayUrl.value.trim(),
      apiKey: $apiKey.value.trim(),
    });
    setStatus("✓ saved", "ok");
  } catch (err) {
    setStatus(`✗ ${err.message}`, "err");
  } finally {
    $save.disabled = false;
  }
});

$test.addEventListener("click", async () => {
  // Persist whatever's in the form before testing, so the test uses the
  // values currently visible to the user — not whatever was last saved.
  await setConfig({
    gatewayUrl: $gatewayUrl.value.trim(),
    apiKey: $apiKey.value.trim(),
  });
  $test.disabled = true;
  setStatus("testing…", "");
  try {
    const data = await testConnection();
    const nodes = data?.graph?.nodes ?? "?";
    setStatus(`✓ reachable · ${nodes} nodes in graph`, "ok");
  } catch (err) {
    setStatus(`✗ ${err.message}`, "err");
  } finally {
    $test.disabled = false;
  }
});

function setStatus(text, kind) {
  $status.textContent = text;
  $status.className = `status${kind ? ` ${kind}` : ""}`;
}
