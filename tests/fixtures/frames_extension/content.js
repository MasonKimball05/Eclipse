chrome.runtime.sendMessage({ type: 'where', href: location.href });
// Marks the page with each token it's sent, so the tests can see which frames a message reached
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === 'mark') document.documentElement.dataset.marked = (document.documentElement.dataset.marked || '') + message.token;
});
