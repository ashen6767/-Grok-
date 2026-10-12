const attached = new Set();
const LOADED_VERSION_KEY = "grokBatchLoadedVersion";
const RELOAD_ATTEMPT_KEY = "grokBatchReloadAttempt";
let desktopOnline = false;
let lastDesktopSeen = 0;
let iconState = "";
let versionCompatible = false;

const ICON_STATES = {
  offline: {color:"#8A98A8", label:"休眠中"},
  ready: {color:"#20B879", label:"已连接 · 等待任务"},
  running: {color:"#2F80ED", label:"正在运行任务"},
  warning: {color:"#E7A520", label:"版本异常或任务已暂停"},
  error: {color:"#E05263", label:"有任务需要处理"}
};

async function setStatusIcon(state) {
  state = ICON_STATES[state] ? state : "offline";
  if (iconState === state) return;
  const status = ICON_STATES[state];
  const imageData = {};
  for (const size of [16, 32, 48, 128]) {
    const source = await fetch(chrome.runtime.getURL(`icon${size}.png`));
    const bitmap = await createImageBitmap(await source.blob());
    const canvas = new OffscreenCanvas(size, size);
    const context = canvas.getContext("2d");
    context.drawImage(bitmap, 0, 0, size, size);
    const radius = Math.max(2, Math.round(size * 0.115));
    const inset = Math.max(1, Math.round(size * 0.07));
    const center = size - radius - inset;
    context.beginPath();
    context.arc(center, center, radius + Math.max(1, Math.round(size * 0.035)), 0, Math.PI * 2);
    context.fillStyle = "#FFFFFF";
    context.fill();
    context.beginPath();
    context.arc(center, center, radius, 0, Math.PI * 2);
    context.fillStyle = status.color;
    context.fill();
    imageData[size] = context.getImageData(0, 0, size, size);
  }
  await chrome.action.setIcon({imageData});
  await chrome.action.setTitle({title:`老七的Grok视频助手 · ${status.label}`});
  iconState = state;
}

async function fetchDesktop(path, timeout = 1600) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`http://127.0.0.1:17843${path}`, {
      cache:"no-store", signal:controller.signal
    });
    if (!response.ok) throw new Error(`Desktop returned ${response.status}`);
    return await response.json();
  } finally { clearTimeout(timer); }
}

async function detachAll() {
  const tabIds = [...attached];
  attached.clear();
  await Promise.allSettled(tabIds.map(tabId => chrome.debugger.detach({tabId})));
}

async function markDesktopOffline() {
  desktopOnline = false;
  versionCompatible = false;
  lastDesktopSeen = 0;
  try { await setStatusIcon("offline"); } catch (_) {}
  if (attached.size) await detachAll();
}

async function refreshGrokTabsAfterUpdate() {
  try {
    const current = chrome.runtime.getManifest().version;
    const saved = await chrome.storage.local.get(LOADED_VERSION_KEY);
    if (saved[LOADED_VERSION_KEY] === current) return;
    await chrome.storage.local.set({[LOADED_VERSION_KEY]:current});
    const tabs = await chrome.tabs.query({url:["https://grok.com/*"]});
    await Promise.allSettled(tabs.map(tab => tab.id ? chrome.tabs.reload(tab.id) : Promise.resolve()));
  } catch (_) { /* A tab may close while the helper is updating. */ }
}

async function clearSuccessfulReloadAttempt(currentVersion) {
  try {
    const saved = await chrome.storage.local.get(RELOAD_ATTEMPT_KEY);
    const attempt = saved[RELOAD_ATTEMPT_KEY];
    if (attempt?.targetVersion && compareVersions(currentVersion, attempt.targetVersion) >= 0)
      await chrome.storage.local.remove(RELOAD_ATTEMPT_KEY);
  } catch (_) { /* Storage can be unavailable while the extension is restarting. */ }
}

function compareVersions(first, second) {
  const left = String(first || "").split(".").map(part => Number.parseInt(part, 10) || 0);
  const right = String(second || "").split(".").map(part => Number.parseInt(part, 10) || 0);
  for (let index = 0; index < Math.max(left.length, right.length); index++) {
    const difference = (left[index] || 0) - (right[index] || 0);
    if (difference) return difference > 0 ? 1 : -1;
  }
  return 0;
}

async function attemptControlledReload(requiredVersion) {
  if (!requiredVersion) return false;
  try {
    const currentVersion = chrome.runtime.getManifest().version;
    // Never let an older desktop instance pull a newer helper backwards.
    if (compareVersions(requiredVersion, currentVersion) <= 0) return false;
    const saved = await chrome.storage.local.get(RELOAD_ATTEMPT_KEY);
    const attempt = saved[RELOAD_ATTEMPT_KEY];
    // One automatic reload per target version. If Chrome still reports the
    // old runtime afterwards, remain in warning state and require one manual
    // reload instead of entering the loop that Chromium eventually disables.
    if (attempt?.targetVersion === requiredVersion) return false;
    await chrome.storage.local.set({
      [RELOAD_ATTEMPT_KEY]: {
        targetVersion: requiredVersion,
        sourceVersion: chrome.runtime.getManifest().version,
        attemptedAt: Date.now()
      }
    });
    if (attached.size) await detachAll();
    chrome.runtime.reload();
    return true;
  } catch (_) {
    return false;
  }
}

async function checkDesktopState() {
  try {
    const data = await fetchDesktop("/api/helper-version");
    desktopOnline = true;
    lastDesktopSeen = Date.now();
    const mismatch = !!data.version && data.version !== chrome.runtime.getManifest().version;
    versionCompatible = !mismatch;
    const state = mismatch
      ? "warning" : (data.helper_state || (data.busy ? "running" : "ready"));
    try { await setStatusIcon(state); } catch (_) {}
    if (mismatch) {
      if (attached.size) await detachAll();
      if (compareVersions(data.version, chrome.runtime.getManifest().version) > 0)
        await attemptControlledReload(data.version);
      else
        await clearSuccessfulReloadAttempt(chrome.runtime.getManifest().version);
    } else {
      await clearSuccessfulReloadAttempt(chrome.runtime.getManifest().version);
      await refreshGrokTabsAfterUpdate();
    }
    return versionCompatible;
  } catch (_) {
    await markDesktopOffline();
    return false;
  }
}

async function desktopAvailable() {
  if (desktopOnline && Date.now() - lastDesktopSeen < 4000) return versionCompatible;
  return checkDesktopState();
}

async function keepTabActive(tabId) {
  if (!desktopOnline) return;
  const source = {tabId};
  const commands = [
    ["Page.setWebLifecycleState", {state:"active"}],
    ["Emulation.setFocusEmulationEnabled", {enabled:true}],
    ["Emulation.setPageVisibilityOverride", {visibilityState:"visible"}]
  ];
  for (const [method, params] of commands) {
    try { await chrome.debugger.sendCommand(source, method, params); }
    catch (_) { /* Older Chromium versions may not expose every override. */ }
  }
}

async function ensureAttached(tabId) {
  if (!await desktopAvailable()) throw new Error("桌面软件未运行，浏览器助手正在休眠");
  if (!attached.has(tabId)) {
    await chrome.debugger.attach({tabId}, "1.3");
    attached.add(tabId);
  }
  await keepTabActive(tabId);
}

chrome.debugger.onDetach.addListener(source => attached.delete(source.tabId));

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !["trusted-input", "keep-active", "desktop-status", "desktop-offline", "automation-idle"].includes(message.type)) return;
  (async () => {
    if (message.type === "desktop-status") {
      desktopOnline = true; lastDesktopSeen = Date.now();
      if (message.requiredVersion && message.requiredVersion !== chrome.runtime.getManifest().version) {
        versionCompatible = false;
        await setStatusIcon("warning");
        if (attached.size) await detachAll();
        const reloading = await attemptControlledReload(message.requiredVersion);
        sendResponse({ok:true, versionMismatch:true, reloading}); return;
      }
      versionCompatible = true;
      await setStatusIcon(message.state); sendResponse({ok:true}); return;
    }
    if (message.type === "desktop-offline") {
      await markDesktopOffline(); sendResponse({ok:true, dormant:true}); return;
    }
    if (message.type === "automation-idle") {
      await detachAll(); sendResponse({ok:true}); return;
    }
    if (!sender.tab?.id) throw new Error("找不到 Grok 标签页");
    const tabId = sender.tab.id;
    await ensureAttached(tabId);
    if (message.type === "keep-active") { sendResponse({ok:true}); return; }
    if (message.action === "move") {
      await chrome.debugger.sendCommand({tabId}, "Input.dispatchMouseEvent", {
        type:"mouseMoved", x:message.x, y:message.y, button:"none", pointerType:"mouse"
      });
    } else if (message.action === "click") {
      await chrome.debugger.sendCommand({tabId}, "Input.dispatchMouseEvent", {
        type:"mousePressed", x:message.x, y:message.y, button:"left", clickCount:1, pointerType:"mouse"
      });
      await chrome.debugger.sendCommand({tabId}, "Input.dispatchMouseEvent", {
        type:"mouseReleased", x:message.x, y:message.y, button:"left", clickCount:1, pointerType:"mouse"
      });
    } else if (message.action === "enter") {
      await chrome.debugger.sendCommand({tabId}, "Input.dispatchKeyEvent", {type:"rawKeyDown", key:"Enter", code:"Enter", windowsVirtualKeyCode:13});
      await chrome.debugger.sendCommand({tabId}, "Input.dispatchKeyEvent", {type:"keyUp", key:"Enter", code:"Enter", windowsVirtualKeyCode:13});
    }
    sendResponse({ok:true});
  })().catch(error => sendResponse({ok:false, error:String(error.message || error)}));
  return true;
});

chrome.tabs.onActivated.addListener(async () => {
  if (!desktopOnline) return;
  for (const tabId of attached) {
    try { await keepTabActive(tabId); } catch (_) {}
  }
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (desktopOnline && attached.has(tabId) && changeInfo.status === "complete") {
    try { await keepTabActive(tabId); } catch (_) {}
  }
});

checkDesktopState();
setInterval(checkDesktopState, 3000);
