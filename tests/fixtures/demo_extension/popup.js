chrome.storage.local.get('visits', ({ visits }) => (document.getElementById('visits').textContent = visits ?? 0));
document.getElementById('options').onclick = () => chrome.runtime.openOptionsPage();
