// Eclipse's extension runtime. Injected into:
//   - the main world of extension pages (pbext://…: popups, options and background pages),
//     after Qt's qwebchannel.js, talking to Python over the web channel
//   - an isolated world per extension (its content scripts) and per userscript, with
//     PB_TOKEN/PB_OWNER defined: there it talks to Python through console messages carrying
//     the world's secret token, and Python answers by calling __pbReply/__pbEvent in the world
// It provides:
//   __pb           the bridge to Python (calls queue until the web channel is connected)
//   __pbChrome()   a chrome.* object for one extension
//   __pbGM()       the GM_* functions for one userscript
(() => {
  if (window.__pb) return;

  // --- Bridge -----------------------------------------------------------------------------
  const queue = [];
  const listeners = {};
  const waiting = {};
  const early = {}; // replies that arrived before anyone was waiting for them
  let host = null;

  const send = (method, args, resolve, reject) =>
    host.call(method, JSON.stringify(args), (raw) => {
      const result = JSON.parse(raw);
      if ('error' in result) reject(new Error(result.error));
      else resolve(result.value);
    });

  const pb = (window.__pb = {
    call: (method, ...args) =>
      new Promise((resolve, reject) =>
        host ? send(method, args, resolve, reject) : queue.push([method, args, resolve, reject])),
    on: (kind, fn) => (listeners[kind] = listeners[kind] || []).push(fn),
    off: (kind, fn) => { const list = listeners[kind] || []; const i = list.indexOf(fn); if (i >= 0) list.splice(i, 1); },
    waitFor: (id) => new Promise((resolve) => {
      if (id in early) { resolve(early[id]); delete early[id]; } else waiting[id] = resolve;
    }),
  });
  // A network request made by Python (no CORS); resolves with {status, statusText, finalUrl, headers, body(base64)}
  pb.request = (method, owner, details) => new Promise((resolve) => {
    let requestId = null;
    const listener = (event) => {
      if (event.id !== requestId) return;
      pb.off('xhr', listener);
      resolve(event);
    };
    pb.on('xhr', listener);
    pb.call(method, owner, details).then((id) => { requestId = id; }, (error) => {
      pb.off('xhr', listener);
      resolve({ type: 'error', status: 0, statusText: String(error.message), headers: '', body: '' });
    });
  });
  pb.decode = (base64) => Uint8Array.from(atob(base64 || ''), (c) => c.charCodeAt(0));

  pb.on('reply', ({ id, value }) => {
    const resolve = waiting[id];
    delete waiting[id];
    if (resolve) resolve(value === null ? undefined : value);
    else early[id] = value === null ? undefined : value;
  });

  const deliver = (kind, payload) =>
    (listeners[kind] || []).slice().forEach((fn) => {
      try { fn(JSON.parse(payload)); } catch (e) { console.error(e); }
    });

  if (typeof PB_TOKEN === 'string') {
    // An isolated world: calls go out as console messages, answers come back via __pbReply.
    // Grab what we use now, before any other script in this world could replace it.
    const log = console.debug.bind(console);
    const stringify = JSON.stringify;
    const here = window.location;
    // A subframe's name is a secret between this runtime and Python, which uses it to find the frame
    const frame = window.top === window ? 'top' : Array.from(crypto.getRandomValues(new Uint32Array(4)), (n) => n.toString(36)).join('');
    const callbacks = {};
    let nextCall = 0;
    host = {
      call: (method, argsJson, callback) => {
        const id = ++nextCall;
        callbacks[id] = callback;
        // Each call says where this frame is now; Python checks it against the frame it found
        log('\u2063pbx:' + stringify([PB_TOKEN, frame, id, method, argsJson, here.href]));
      },
    };
    const fixed = (name, fn) => Object.defineProperty(window, name, { value: fn, writable: false, configurable: false });
    fixed('__pbFrameIs', (name) => name === frame);  // Python asking each frame "is this you?"
    fixed('__pbReply', (target, id, raw) => {
      if (target !== frame) return;
      const callback = callbacks[id];
      delete callbacks[id];
      if (callback) callback(raw);
    });
    fixed('__pbEvent', (target, kind, payload) => { if (target === frame) deliver(kind, payload); });
    pb.call('hello', PB_OWNER);
  } else {
    // An extension page: the web channel
    const connect = () => {
      if (typeof qt === 'undefined' || !qt.webChannelTransport) return setTimeout(connect, 25);
      new QWebChannel(qt.webChannelTransport, (channel) => {
        host = channel.objects.pbHost;
        host.event.connect(deliver);
        queue.splice(0).forEach((q) => send(...q));
      });
    };
    connect();
  }

  // --- chrome.* -------------------------------------------------------------------------------
  const makeEvent = (kind) => {
    const fns = [];
    if (kind) pb.on(kind, (args) => fns.slice().forEach((fn) => fn(...args)));
    return {
      addListener: (fn) => fns.push(fn),
      removeListener: (fn) => { const i = fns.indexOf(fn); if (i >= 0) fns.splice(i, 1); },
      hasListener: (fn) => fns.includes(fn),
      hasListeners: () => fns.length > 0,
      _fns: fns,
    };
  };

  // An inert API namespace: events never fire, functions do nothing (lists come back empty),
  // and enum-like objects (tabGroups.Color.GREY) answer with the key in lower case
  const stub = () => new Proxy({}, {
    get: (target, key) => {
      if (typeof key !== 'string' || key === 'then' || key === 'toJSON') return undefined;
      if (key in target) return target[key];
      if (/^on[A-Z]/.test(key)) return (target[key] = makeEvent(null));
      if (/^[A-Z]/.test(key)) return (target[key] = new Proxy({}, { get: (_, k) => (typeof k === 'string' ? k.toLowerCase() : undefined) }));
      const empty = /^(get\w*s|getAll|query|search)$/.test(key) ? [] : undefined;
      return (target[key] = (...args) => {
        const callback = typeof args[args.length - 1] === 'function' ? args.pop() : undefined;
        if (callback) setTimeout(() => callback(empty), 0);
        return Promise.resolve(empty);
      });
    },
  });

  window.__pbChrome = (id, meta) => {
    const call = (method, ...args) => pb.call(method, id, ...args);
    let lastError;
    const done = (promise, callback) => {
      if (typeof callback !== 'function') return promise;
      promise.then(
        (value) => callback(value),
        (error) => { lastError = { message: error.message }; try { callback(); } finally { lastError = undefined; } });
    };
    const lastArgCallback = (args) => (typeof args[args.length - 1] === 'function' ? args.pop() : undefined);

    const area = (name) => ({
      get: (keys, callback) => {
        if (typeof keys === 'function') [keys, callback] = [null, keys];
        return done(call('storage.get', name, keys === undefined ? null : keys), callback);
      },
      set: (items, callback) => done(call('storage.set', name, items), callback),
      remove: (keys, callback) => done(call('storage.remove', name, keys), callback),
      clear: (callback) => done(call('storage.clear', name), callback),
      getBytesInUse: (keys, callback) => done(Promise.resolve(0), typeof keys === 'function' ? keys : callback),
      onChanged: makeEvent(`storage.${name}:${id}`),
    });

    // Messages arriving for this extension (from its other pages, or its content scripts)
    const onMessage = makeEvent(null);
    // Messages from websites the extension lists in externally_connectable
    const onMessageExternal = makeEvent(null);
    const answer = (event) => ([message, sender, replyId]) => {
      let answered = false;
      let pending = false;
      const sendResponse = (value) => {
        if (answered) return;
        answered = true;
        call('runtime.reply', replyId, value === undefined ? null : value);
      };
      for (const fn of event._fns.slice()) {
        let result;
        try { result = fn(message, sender, sendResponse); } catch (e) { console.error(e); continue; }
        if (result === true) pending = true;
        else if (result && typeof result.then === 'function') { pending = true; result.then(sendResponse, () => sendResponse()); }
      }
      if (!pending && !answered) call('runtime.noReply', replyId);
    };
    pb.on('message:' + id, answer(onMessage));
    pb.on('messageExternal:' + id, answer(onMessageExternal));

    const sendMessage = (...args) => {
      const callback = lastArgCallback(args);
      // sendMessage(message) or sendMessage(extensionId, message, options?)
      const message = args.length >= 2 && typeof args[0] === 'string' && typeof args[1] !== 'undefined' &&
        !(args.length === 2 && args[1] && typeof args[1] === 'object' && 'includeTlsChannelId' in args[1])
        ? args[1] : args[0];
      return done(call('runtime.sendMessage', message).then((replyId) => pb.waitFor(replyId)), callback);
    };

    const messages = meta.messages || {};
    const getMessage = (name, substitutions) => {
      const entry = messages[String(name).toLowerCase()];
      if (!entry) return '';
      const subs = Array.isArray(substitutions) ? substitutions : substitutions === undefined ? [] : [substitutions];
      let text = entry.message || '';
      for (const [key, placeholder] of Object.entries(entry.placeholders || {}))
        text = text.replace(new RegExp('\\$' + key + '\\$', 'gi'), placeholder.content || '');
      return text.replace(/\$(\d)/g, (_, n) => subs[Number(n) - 1] ?? '');
    };

    const alarms = {};
    const onAlarm = makeEvent(null);
    const getURL = (path) => `pbext://${id}/` + String(path).replace(/^\//, '');

    const chrome = {
      runtime: {
        id, getURL, sendMessage, onMessage,
        getManifest: () => meta.manifest,
        get lastError() { return lastError; },
        onInstalled: makeEvent('installed:' + id),
        onStartup: makeEvent(null), onMessageExternal, onConnect: makeEvent(null),
        onConnectExternal: makeEvent(null), onSuspend: makeEvent(null), onUpdateAvailable: makeEvent(null),
        getPlatformInfo: (callback) => done(Promise.resolve({ os: 'mac', arch: 'arm', nacl_arch: 'arm' }), callback),
        openOptionsPage: (callback) => done(call('runtime.openOptionsPage'), callback),
        setUninstallURL: (url, callback) => done(Promise.resolve(), callback),
        reload: () => location.reload(),
      },
      storage: {
        local: area('local'), sync: area('sync'), session: area('session'), managed: area('managed'),
        onChanged: makeEvent('storage:' + id),
      },
      i18n: {
        getMessage,
        getUILanguage: () => navigator.language || 'en',
        getAcceptLanguages: (callback) => done(Promise.resolve([navigator.language || 'en']), callback),
        detectLanguage: (text, callback) => done(Promise.resolve({ isReliable: false, languages: [] }), callback),
      },
      tabs: {
        query: (info, callback) => done(call('tabs.query', info || {}), callback),
        get: (tabId, callback) => done(call('tabs.get', tabId), callback),
        getCurrent: (callback) => done(call('tabs.current'), callback),
        create: (props, callback) => done(call('tabs.create', props || {}), callback),
        update: (...args) => {
          const callback = lastArgCallback(args);
          const [tabId, props] = args.length === 1 ? [null, args[0]] : args;
          return done(call('tabs.update', tabId, props || {}), callback);
        },
        remove: (tabIds, callback) => done(call('tabs.remove', [].concat(tabIds)), callback),
        reload: (tabId, props, callback) => done(call('tabs.update', tabId ?? null, { reload: true }), typeof props === 'function' ? props : callback),
        sendMessage: (tabId, message, ...rest) => {
          const callback = lastArgCallback(rest);
          const frameId = rest[0] && typeof rest[0] === 'object' && rest[0].frameId !== undefined ? rest[0].frameId : null;
          return done(call('tabs.sendMessage', tabId, message, { frameId }).then((replyId) => pb.waitFor(replyId)), callback);
        },
        onUpdated: makeEvent('tabs.updated'), onActivated: makeEvent('tabs.activated'),
        onRemoved: makeEvent('tabs.removed'), onCreated: makeEvent(null), onReplaced: makeEvent(null),
        TAB_ID_NONE: -1,
      },
      windows: {
        WINDOW_ID_CURRENT: -2, WINDOW_ID_NONE: -1,
        getCurrent: (_, callback) => done(Promise.resolve({ id: 1, focused: true, type: 'normal' }), typeof _ === 'function' ? _ : callback),
        getLastFocused: (_, callback) => done(Promise.resolve({ id: 1, focused: true, type: 'normal' }), typeof _ === 'function' ? _ : callback),
        getAll: (_, callback) => done(Promise.resolve([{ id: 1, focused: true, type: 'normal' }]), typeof _ === 'function' ? _ : callback),
        create: (props, callback) => done(call('tabs.create', { url: [].concat((props || {}).url || [])[0] }), callback),
        onFocusChanged: makeEvent(null), onRemoved: makeEvent(null), onCreated: makeEvent(null),
      },
      action: {
        setBadgeText: (details, callback) => done(call('action.setBadge', details || {}), callback),
        setBadgeBackgroundColor: (details, callback) => done(call('action.setBadgeColor', details || {}), callback),
        getBadgeText: (details, callback) => done(call('action.getBadge'), callback),
        setTitle: (details, callback) => done(Promise.resolve(), callback),
        setIcon: (details, callback) => done(Promise.resolve(), callback),
        setPopup: (details, callback) => done(Promise.resolve(), callback),
        enable: () => {}, disable: () => {},
        onClicked: makeEvent('action.clicked:' + id),
      },
      alarms: {
        create: (...args) => {
          const info = args.pop() || {};
          const name = args[0] || '';
          clearTimeout(alarms[name]);
          const fire = () => onAlarm._fns.slice().forEach((fn) => fn({ name, scheduledTime: Date.now() }));
          const first = (info.when ? info.when - Date.now() : (info.delayInMinutes ?? info.periodInMinutes ?? 0) * 60000);
          alarms[name] = setTimeout(function tick() {
            const self = alarms[name];
            fire();
            // Re-arm only if a listener didn't clear (or re-create) the alarm while it was firing
            if (info.periodInMinutes && alarms[name] === self) alarms[name] = setTimeout(tick, info.periodInMinutes * 60000);
          }, Math.max(first, 0));
          return Promise.resolve();
        },
        clear: (name, callback) => { clearTimeout(alarms[name || '']); delete alarms[name || '']; return done(Promise.resolve(true), callback); },
        clearAll: (callback) => { Object.keys(alarms).forEach((k) => { clearTimeout(alarms[k]); delete alarms[k]; }); return done(Promise.resolve(true), callback); },
        get: (name, callback) => done(Promise.resolve(alarms[name] ? { name } : undefined), callback),
        getAll: (callback) => done(Promise.resolve(Object.keys(alarms).map((name) => ({ name }))), callback),
        onAlarm,
      },
      notifications: {
        create: (...args) => {
          const callback = lastArgCallback(args);
          const options = args[args.length - 1] || {};
          return done(call('notify', options.title || '', options.message || ''), callback);
        },
        clear: (_, callback) => done(Promise.resolve(true), callback),
        onClicked: makeEvent(null), onClosed: makeEvent(null), onButtonClicked: makeEvent(null),
      },
      permissions: {
        contains: (_, callback) => done(Promise.resolve(true), callback),
        request: (_, callback) => done(Promise.resolve(true), callback),
        getAll: (callback) => done(Promise.resolve({ permissions: (meta.manifest.permissions || []), origins: [] }), callback),
        onAdded: makeEvent(null), onRemoved: makeEvent(null),
      },
      extension: { getURL, isAllowedIncognitoAccess: (callback) => done(Promise.resolve(false), callback), inIncognitoContext: false },
    };
    // chrome.scripting: code runs in the tab's isolated world, like a content script
    const viaReply = (promise) => promise.then((replyId) => pb.waitFor(replyId));
    const fileText = (files) => Promise.all((files || []).map((f) => fetch(getURL(f)).then((r) => r.text())));
    const tabOf = (injection) => ((injection && injection.target) || {}).tabId ?? null;
    chrome.scripting = {
      executeScript: (injection, callback) => done((async () => {
        const body = injection.files
          ? `(() => {\n${(await fileText(injection.files)).join('\n;\n')}\n})()`
          : `(${injection.func || injection.function})(...${JSON.stringify(injection.args || [])})`;
        const main = injection.world === 'MAIN';  // the page's own world: no extension APIs there
        const code = main ? body
          : `(() => { const chrome = window.__pbApi || (window.__pbChrome ? window.__pbChrome(${JSON.stringify(id)}, ${JSON.stringify(meta)}) : undefined); const browser = chrome; return ${body}; })()`;
        const allFrames = !!(injection.target && injection.target.allFrames);
        const result = await viaReply(call('scripting.execute', tabOf(injection), code, main, allFrames));
        return [{ frameId: 0, documentId: '', result }];
      })(), callback),
      insertCSS: (injection, callback) => done((async () => {
        const css = injection.css || (await fileText(injection.files)).join('\n');
        await call('scripting.css', tabOf(injection), css, false);
      })(), callback),
      removeCSS: (injection, callback) => done((async () => {
        const css = injection.css || (await fileText(injection.files)).join('\n');
        await call('scripting.css', tabOf(injection), css, true);
      })(), callback),
      registerContentScripts: (_, callback) => done(Promise.resolve(), callback),
      unregisterContentScripts: (_, callback) => done(Promise.resolve(), typeof _ === 'function' ? _ : callback),
      getRegisteredContentScripts: (_, callback) => done(Promise.resolve([]), typeof _ === 'function' ? _ : callback),
      updateContentScripts: (_, callback) => done(Promise.resolve(), callback),
    };
    chrome.tabs.executeScript = (tabId, details, callback) => {
      if (typeof tabId === 'object') [tabId, details, callback] = [null, tabId, details];
      const injection = { target: { tabId }, ...(details.file ? { files: [details.file] } : { func: new Function(details.code) }) };
      return done(chrome.scripting.executeScript(injection).then((r) => r.map((f) => f.result)), callback);
    };
    chrome.tabs.insertCSS = (tabId, details, callback) => {
      if (typeof tabId === 'object') [tabId, details, callback] = [null, tabId, details];
      return done(chrome.scripting.insertCSS({ target: { tabId }, css: details.code, files: details.file && [details.file] }), callback);
    };

    // Offscreen documents: a hidden page, like the background page
    chrome.offscreen = {
      createDocument: (options, callback) => done(call('offscreen.create', options.url), callback),
      closeDocument: (callback) => done(call('offscreen.close'), callback),
      hasDocument: (callback) => done(call('offscreen.has'), callback),
      Reason: new Proxy({}, { get: (_, k) => k }),
    };
    chrome.runtime.getContexts = (filter, callback) => done(call('offscreen.has').then((has) =>
      has ? [{ contextType: 'OFFSCREEN_DOCUMENT', documentUrl: '' }] : []), callback);
    Object.assign(chrome.runtime, {
      OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
      OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
      PlatformOs: { MAC: 'mac', WIN: 'win', LINUX: 'linux', CROS: 'cros', ANDROID: 'android', OPENBSD: 'openbsd' },
      PlatformArch: { ARM: 'arm', ARM64: 'arm64', X86_32: 'x86-32', X86_64: 'x86-64' },
    });
    chrome.tabs.TabStatus = { LOADING: 'loading', UNLOADED: 'unloaded', COMPLETE: 'complete' };
    chrome.runtime.ContextType = { OFFSCREEN_DOCUMENT: 'OFFSCREEN_DOCUMENT', BACKGROUND: 'BACKGROUND', POPUP: 'POPUP', TAB: 'TAB' };

    chrome.downloads = Object.assign(stub(), {
      download: (options, callback) => done(call('downloads.download', options || {}), callback),
    });

    // webNavigation, approximated from tab updates (no per-frame detail)
    const nav = (name) => makeEvent(null);
    chrome.webNavigation = {
      getAllFrames: (details, callback) => done(chrome.tabs.get(details.tabId).then((tab) =>
        [{ frameId: 0, parentFrameId: -1, url: tab.url, errorOccurred: false, processId: 0 }]), callback),
      getFrame: (details, callback) => done(chrome.tabs.get(details.tabId).then((tab) =>
        (details.frameId ? null : { frameId: 0, parentFrameId: -1, url: tab.url, errorOccurred: false })), callback),
      onBeforeNavigate: nav(), onCommitted: nav(), onDOMContentLoaded: nav(), onCompleted: nav(),
      onHistoryStateUpdated: nav(), onReferenceFragmentUpdated: nav(), onErrorOccurred: nav(),
      onCreatedNavigationTarget: nav(), onTabReplaced: nav(),
    };
    pb.on('tabs.updated', ([tabId, changes, tab]) => {
      const details = { tabId, frameId: 0, parentFrameId: -1, url: tab.url, timeStamp: Date.now(), processId: 0 };
      const fire = (event) => event._fns.slice().forEach((fn) => { try { fn(details); } catch (e) { console.error(e); } });
      const n = chrome.webNavigation;
      if (changes.url) [n.onBeforeNavigate, n.onCommitted].forEach(fire);
      if (changes.status === 'complete') [n.onDOMContentLoaded, n.onCompleted].forEach(fire);
    });

    // chrome.declarativeNetRequest: the rules run in the browser's request interceptor (netrules.py)
    const dnr = (name) => (...args) => {
      const callback = lastArgCallback(args);
      return done(call('dnr.' + name, args[0] === undefined ? null : args[0]), callback);
    };
    chrome.declarativeNetRequest = Object.assign(stub(), {
      updateDynamicRules: dnr('updateDynamic'), getDynamicRules: dnr('getDynamic'),
      updateSessionRules: dnr('updateSession'), getSessionRules: dnr('getSession'),
      updateEnabledRulesets: dnr('updateEnabledRulesets'), getEnabledRulesets: dnr('getEnabledRulesets'),
      updateStaticRules: dnr('updateStatic'), getDisabledRuleIds: dnr('getDisabledRuleIds'),
      setExtensionActionOptions: dnr('setActionOptions'), getMatchedRules: dnr('getMatchedRules'),
      isRegexSupported: dnr('isRegexSupported'), testMatchOutcome: dnr('testMatchOutcome'),
      getAvailableStaticRuleCount: (callback) => done(Promise.resolve(300000), callback),
      MAX_NUMBER_OF_DYNAMIC_RULES: 30000, MAX_NUMBER_OF_SESSION_RULES: 5000, MAX_NUMBER_OF_REGEX_RULES: 1000,
      MAX_NUMBER_OF_ENABLED_STATIC_RULESETS: 50, MAX_NUMBER_OF_STATIC_RULESETS: 100,
      GUARANTEED_MINIMUM_STATIC_RULES: 30000, DYNAMIC_RULESET_ID: '_dynamic', SESSION_RULESET_ID: '_session',
    });

    chrome.commands = {
      getAll: (callback) => done(Promise.resolve(Object.entries(meta.manifest.commands || {}).map(([name, spec]) => ({
        name, description: spec.description || '', shortcut: ((spec.suggested_key || {}).mac || (spec.suggested_key || {}).default || '') }))), callback),
      onCommand: makeEvent('commands:' + id),
    };

    // chrome.webRequest: watching requests works; blocking or changing them from a listener doesn't
    // (Qt won't pause a request), and the response-side events never fire
    const webRequestListeners = {};
    pb.on('webRequest:' + id, ([listenerId, details]) => {
      const fn = webRequestListeners[listenerId];
      if (fn) try { fn(details); } catch (e) { console.error(e); }
    });
    const requestEvent = (name) => {
      const registered = new Map();  // listener → its id(s) in Python
      return {
        addListener: (fn, filter, extraInfoSpec) => {
          const ids = [];
          registered.set(fn, ids);
          call('webRequest.listen', name, filter || {}, extraInfoSpec || []).then((listenerId) => {
            if (listenerId === null || listenerId === undefined) return;
            webRequestListeners[listenerId] = fn;
            ids.push(listenerId);
          });
        },
        removeListener: (fn) => {
          (registered.get(fn) || []).forEach((listenerId) => { delete webRequestListeners[listenerId]; call('webRequest.unlisten', listenerId); });
          registered.delete(fn);
        },
        hasListener: (fn) => registered.has(fn),
        hasListeners: () => registered.size > 0,
      };
    };
    chrome.webRequest = Object.assign(stub(), {
      onBeforeRequest: requestEvent('onBeforeRequest'), onBeforeSendHeaders: requestEvent('onBeforeSendHeaders'),
      onSendHeaders: requestEvent('onSendHeaders'), onHeadersReceived: requestEvent('onHeadersReceived'),
      onAuthRequired: requestEvent('onAuthRequired'), onResponseStarted: requestEvent('onResponseStarted'),
      onBeforeRedirect: requestEvent('onBeforeRedirect'), onCompleted: requestEvent('onCompleted'),
      onErrorOccurred: requestEvent('onErrorOccurred'),
      handlerBehaviorChanged: (callback) => done(Promise.resolve(), callback),
      MAX_HANDLER_BEHAVIOR_CHANGED_CALLS_PER_10_MINUTES: 20,
    });

    chrome.action.getUserSettings = (callback) => done(Promise.resolve({ isOnToolbar: true }), callback);
    chrome.action.openPopup = (_, callback) => done(Promise.resolve(), typeof _ === 'function' ? _ : callback);

    // APIs that need Chromium's internals: present, so code that touches them doesn't crash, but inert
    for (const name of ['sidePanel', 'tabGroups', 'cookies', 'identity',
                        'privacy', 'management', 'history', 'bookmarks', 'declarativeContent', 'debugger',
                        'contentSettings', 'search', 'sessions', 'topSites', 'tts', 'idle', 'power',
                        'browsingData', 'proxy', 'gcm', 'instanceID', 'devtools', 'userScripts', 'system']) {
      if (!(name in chrome)) chrome[name] = stub();
    }

    // Long-lived ports: runtime.connect / tabs.connect, answered by onConnect elsewhere
    const fire = (event, ...args) => event._fns.slice().forEach((fn) => { try { fn(...args); } catch (e) { console.error(e); } });
    const makePort = (name, sender, ready) => {
      const port = { name, sender, onMessage: makeEvent(null), onDisconnect: makeEvent(null) };
      let open = true;
      let portId = null;
      const received = ([message]) => fire(port.onMessage, message, port);
      const closed = (details) => {
        if (!open) return;
        open = false;
        cleanup();
        const error = details && details[0];  // why it closed (e.g. a native helper that couldn't start)
        if (error) lastError = { message: error };
        try { fire(port.onDisconnect, port); } finally { if (error) lastError = undefined; }
      };
      const cleanup = () => { pb.off('port:' + portId, received); pb.off('port.disconnect:' + portId, closed); };
      ready.then((pid) => { portId = pid; pb.on('port:' + pid, received); pb.on('port.disconnect:' + pid, closed); });
      port.postMessage = (message) => {
        if (!open) throw new Error('Attempting to use a disconnected port object');
        ready.then((pid) => call('port.post', pid, message === undefined ? null : message));
      };
      port.disconnect = () => {
        if (!open) return;
        open = false;
        ready.then((pid) => { call('port.close', pid); cleanup(); });
      };
      return port;
    };
    const connect = (tabId, info) => makePort((info && info.name) || '', undefined,
      call('port.open', (info && info.name) || '', tabId, info && info.frameId !== undefined ? info.frameId : null));
    // Native messaging: a helper program on this Mac (Python checks it's registered and allows us)
    chrome.runtime.connectNative = (name) => makePort(name, undefined, call('native.connect', name));
    chrome.runtime.sendNativeMessage = (name, message, callback) => done(
      call('native.send', name, message).then((replyId) => pb.waitFor(replyId)).then((value) => {
        if (value && value.__pbNativeError) throw new Error(value.__pbNativeError);
        return value;
      }), callback);
    chrome.runtime.connect = (...args) => connect(null, args.find((a) => a && typeof a === 'object'));
    chrome.tabs.connect = (tabId, info) => connect(tabId, info);
    pb.on('connect:' + id, ([portId, name, sender]) => {
      if (!chrome.runtime.onConnect.hasListeners()) { call('port.reject', portId); return; }
      call('port.accept', portId);
      const port = makePort(name, sender, Promise.resolve(portId));
      fire(chrome.runtime.onConnect, port);
    });

    // Context menus: items show up in the page's right-click menu
    const menuClicks = {};
    chrome.contextMenus = {
      create: (props, callback) => {
        const itemId = props.id ?? Math.floor(Math.random() * 1e9);
        if (typeof props.onclick === 'function') menuClicks[itemId] = props.onclick;
        const { onclick, ...rest } = props;
        done(call('menus.create', { ...rest, id: itemId }), callback);
        return itemId;
      },
      update: (itemId, props, callback) => {
        if (typeof props.onclick === 'function') menuClicks[itemId] = props.onclick;
        const { onclick, ...rest } = props;
        return done(call('menus.update', itemId, rest), callback);
      },
      remove: (itemId, callback) => done(call('menus.remove', itemId), callback),
      removeAll: (callback) => done(call('menus.removeAll'), callback),
      onClicked: makeEvent('menus.clicked:' + id),
      onShown: makeEvent(null), onHidden: makeEvent(null), refresh: () => {},
      ContextType: new Proxy({}, { get: (_, k) => (typeof k === 'string' ? k.toLowerCase() : undefined) }),
      ItemType: new Proxy({}, { get: (_, k) => (typeof k === 'string' ? k.toLowerCase() : undefined) }),
      ACTION_MENU_TOP_LEVEL_LIMIT: 6,
    };
    pb.on('menus.clicked:' + id, ([info, tab]) => { const fn = menuClicks[info.menuItemId]; if (fn) fn(info, tab); });

    chrome.browserAction = chrome.action;
    chrome.pageAction = chrome.action;
    chrome.menus = chrome.contextMenus;
    return chrome;
  };

  // --- GM_* (userscripts) ---------------------------------------------------------------------
  window.__pbGM = (scriptId, info, values) => {
    const call = (method, ...args) => pb.call(method, scriptId, ...args);
    const GM_getValue = (key, fallback) => (key in values ? values[key] : fallback);
    const GM_setValue = (key, value) => { values[key] = value; call('gm.setValue', key, value); };
    const GM_deleteValue = (key) => { delete values[key]; call('gm.deleteValue', key); };
    const GM_listValues = () => Object.keys(values);
    const GM_addStyle = (css) => {
      const style = document.createElement('style');
      style.textContent = css;
      (document.head || document.documentElement).appendChild(style);
      return style;
    };
    const GM_openInTab = (url, options) => {
      const background = typeof options === 'boolean' ? options : !!(options && (options.active === false || options.loadInBackground));
      call('gm.openInTab', String(url), background);
      return { close() {}, closed: false };
    };
    const GM_setClipboard = (text) => call('gm.setClipboard', String(text));
    const GM_notification = (details, title) => {
      const d = typeof details === 'string' ? { text: details, title } : (details || {});
      call('gm.notification', d.title || info.script.name, d.text || '');
    };
    const GM_xmlhttpRequest = (details) => {
      let aborted = false;
      const toResponse = (r) => {
        const bytes = pb.decode(r.body);
        const text = new TextDecoder().decode(bytes);
        const type = details.responseType || '';
        let response = text;
        if (type === 'json') { try { response = JSON.parse(text); } catch (e) { response = undefined; } }
        else if (type === 'arraybuffer') response = bytes.buffer;
        else if (type === 'blob') response = new Blob([bytes]);
        return { status: r.status, statusText: r.statusText, readyState: 4, finalUrl: r.finalUrl,
                 responseHeaders: r.headers, responseText: text, response, context: details.context };
      };
      pb.request('gm.xhr', scriptId, {
        method: details.method || 'GET', url: String(details.url), headers: details.headers || {},
        data: details.data == null ? null : String(details.data), timeout: details.timeout || 0,
      }).then((event) => {
        if (aborted) return;
        const response = toResponse(event);
        if (event.type === 'load') { details.onload && details.onload(response); }
        else { details.onerror && details.onerror(response); }
        details.onloadend && details.onloadend(response);
      });
      return { abort: () => { aborted = true; details.onabort && details.onabort(); } };
    };
    const promised = (fn) => (...args) => Promise.resolve(fn(...args));
    const GM = {
      info,
      getValue: promised(GM_getValue), setValue: promised(GM_setValue), deleteValue: promised(GM_deleteValue),
      listValues: promised(GM_listValues), addStyle: promised(GM_addStyle), openInTab: promised(GM_openInTab),
      setClipboard: promised(GM_setClipboard), notification: promised(GM_notification),
      xmlHttpRequest: (details) => new Promise((resolve, reject) => GM_xmlhttpRequest(
        { ...details, onload: (r) => { details.onload && details.onload(r); resolve(r); },
          onerror: (r) => { details.onerror && details.onerror(r); reject(r); } })),
    };
    return { GM, GM_info: info, GM_getValue, GM_setValue, GM_deleteValue, GM_listValues, GM_addStyle,
             GM_xmlhttpRequest, GM_openInTab, GM_setClipboard, GM_notification, unsafeWindow: window };
  };
})();
