// Service worker — owns the right-click context menu and notification UX.

import { ingestBrowser } from "./lib/api.js";

const MENU_SELECTION = "synapse-save-selection";
const MENU_PAGE = "synapse-save-page";
const MENU_LINK = "synapse-save-link";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_SELECTION,
    title: "Save selection to Synapse",
    contexts: ["selection"],
  });
  chrome.contextMenus.create({
    id: MENU_PAGE,
    title: "Save this page to Synapse",
    contexts: ["page"],
  });
  chrome.contextMenus.create({
    id: MENU_LINK,
    title: "Save link to Synapse",
    contexts: ["link"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  try {
    const payload = buildPayload(info, tab);
    if (!payload) return;
    await ingestBrowser(payload);
    notify("✓ Saved to Synapse", payload.title || payload.url || "");
  } catch (err) {
    notify("✗ Synapse save failed", String(err?.message || err));
  }
});

function buildPayload(info, tab) {
  if (info.menuItemId === MENU_SELECTION && info.selectionText) {
    return {
      url: info.pageUrl || tab?.url || "",
      title: tab?.title || "",
      content: info.selectionText,
    };
  }
  if (info.menuItemId === MENU_LINK && info.linkUrl) {
    return {
      url: info.linkUrl,
      title: info.selectionText || tab?.title || info.linkUrl,
      content: info.selectionText || `Link from ${tab?.url || ""}`,
    };
  }
  if (info.menuItemId === MENU_PAGE && tab?.url) {
    return {
      url: tab.url,
      title: tab.title || "",
      content: tab.title || tab.url,
    };
  }
  return null;
}

function notify(title, message) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon-128.png",
    title,
    message: message.slice(0, 160),
  });
}
