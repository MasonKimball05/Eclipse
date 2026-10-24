// Counts the pages the content script reports, and answers with the running total
chrome.runtime.onInstalled.addListener(() => chrome.storage.local.set({ visits: 0, installed: true }));
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type !== 'visit') return;
  chrome.storage.local.get({ visits: 0 }).then(({ visits }) => {
    chrome.storage.local.set({ visits: visits + 1, last: sender.url });
    chrome.action.setBadgeText({ text: String(visits + 1) });
    sendResponse({ visits: visits + 1, greeting: chrome.i18n.getMessage('greeting', ['tests']) });
  });
  return true;
});

// A long-lived port: answers each message with "pong:<message>"
chrome.runtime.onConnect.addListener((port) => {
  port.onMessage.addListener((message) => port.postMessage('pong:' + message + ':' + port.name));
});

// A right-click menu item for selected text
chrome.runtime.onInstalled.addListener(() =>
  chrome.contextMenus.create({ id: 'demo-lookup', title: 'Look up “%s”', contexts: ['selection'] }));
chrome.contextMenus.onClicked.addListener((info) =>
  chrome.storage.local.set({ lookedUp: info.selectionText, menuItem: info.menuItemId }));

// Websites listed in externally_connectable can message the extension directly
chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  sendResponse({ echo: message, from: sender.origin, hasId: 'id' in sender });
});

// Watch (not block) requests for images, keeping the last one seen
chrome.webRequest.onBeforeRequest.addListener((details) => {
  chrome.storage.session.set({ lastImage: details.url, lastImageTab: details.tabId });
}, { urls: ['<all_urls>'], types: ['image'] });
