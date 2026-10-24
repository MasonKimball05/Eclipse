document.documentElement.dataset.demoExtension = 'loaded';
chrome.runtime.sendMessage({ type: 'visit' }, (reply) => {
  document.documentElement.dataset.demoVisits = String(reply && reply.visits);
  document.documentElement.dataset.demoGreeting = reply ? reply.greeting : 'none';
});

const port = chrome.runtime.connect({ name: 'demo' });
port.onMessage.addListener((message) => { document.documentElement.dataset.demoPort = message; });
port.postMessage('ping');
