import { ingestBrowser } from "./lib/api.js";

const $content = document.getElementById("content");
const $save = document.getElementById("save");
const $savePage = document.getElementById("save-page");
const $status = document.getElementById("status");
const $openOptions = document.getElementById("open-options");

$openOptions.addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});

$save.addEventListener("click", async () => {
  const text = $content.value.trim();
  if (!text) {
    setStatus("Type something first.", "err");
    return;
  }
  const tab = await getActiveTab();
  await submit({
    url: tab?.url || "",
    title: tab?.title || "Manual capture",
    content: text,
  });
});

$savePage.addEventListener("click", async () => {
  const tab = await getActiveTab();
  if (!tab?.url) {
    setStatus("No active tab.", "err");
    return;
  }
  await submit({
    url: tab.url,
    title: tab.title || tab.url,
    content: tab.title || tab.url,
  });
});

async function submit(payload) {
  $save.disabled = true;
  $savePage.disabled = true;
  setStatus("saving…", "");
  try {
    await ingestBrowser(payload);
    setStatus("✓ saved to inbox", "ok");
    $content.value = "";
    setTimeout(() => window.close(), 600);
  } catch (err) {
    setStatus(`✗ ${err.message}`, "err");
  } finally {
    $save.disabled = false;
    $savePage.disabled = false;
  }
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function setStatus(text, kind) {
  $status.textContent = text;
  $status.className = `status${kind ? ` ${kind}` : ""}`;
}
