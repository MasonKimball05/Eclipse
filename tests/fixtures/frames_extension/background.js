// Keeps what each frame's content script was seen as, by the address it gave itself
self.seen = {};
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type !== 'where') return;
  self.seen[message.href] = { url: sender.url, origin: sender.origin, frameId: sender.frameId, tabId: sender.tab && sender.tab.id };
  sendResponse(sender.frameId);
});
