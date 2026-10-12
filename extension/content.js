(() => {
  if (window.__grokBatchAssistant) return;
  window.__grokBatchAssistant = true;
  const API = "http://127.0.0.1:17843/api";
  const CLIENT = sessionStorage.grokBatchClient || (sessionStorage.grokBatchClient = crypto.randomUUID());
  const HELPER_VERSION = chrome.runtime.getManifest().version;
  const BROWSER_NAME = /Edg\//.test(navigator.userAgent) ? "Edge" : "Chrome";
  let busy = false;
  let controller = false;
  let desktopOnline = false;
  let automationActive = false;
  let offlineNotified = false;
  let trustedInputReady = false;
  let versionMismatch = false;
  let stopped = false;
  let tickTimer = 0;
  let reportedIconState = "";
  const LAST_SUBMIT_AT_KEY = "grokBatchLastSubmitAt";
  const LAST_SUBMIT_INTERVAL_KEY = "grokBatchLastSubmitInterval";
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const norm = value => (value || "").replace(/\s+/g, " ").trim().toLowerCase();
  const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const all = selector => [...document.querySelectorAll(selector)].filter(visible);
  const textOf = el => norm([el.innerText, el.textContent, el.getAttribute?.("aria-label"), el.getAttribute?.("title")].filter(Boolean).join(" "));
  function extensionAlive() {
    try { return !!chrome?.runtime?.id; }
    catch (_) { return false; }
  }
  async function runtimeMessage(message) {
    if (!extensionAlive()) { stopped = true; throw new Error("扩展已更新，等待页面重新载入"); }
    try { return await chrome.runtime.sendMessage(message); }
    catch (error) {
      if (!extensionAlive() || /extension context invalidated/i.test(String(error?.message || error))) stopped = true;
      throw error;
    }
  }

  async function api(path, options = {}) {
    const response = await fetch(API + path, options);
    if (!response.ok) throw new Error(`控制台接口错误 ${response.status}`);
    return response.headers.get("content-type")?.includes("json") ? response.json() : response.blob();
  }
  async function event(id, status, extra = {}) {
    return api("/event", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({id,status,...extra})});
  }
  function findText(labels, selectors = "button,[role=button],[role=menuitem],a,div") {
    const wanted = labels.map(norm);
    return all(selectors).find(el => wanted.some(label => textOf(el) === label || textOf(el).includes(label)));
  }
  function exactText(labels, root = document) {
    const wanted = labels.map(norm);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) { return wanted.includes(norm(node.nodeValue)) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP; }
    });
    let node;
    while ((node = walker.nextNode())) {
      const element = node.parentElement;
      if (!visible(element)) continue;
      let clickable = element.closest("a,button,[role=button],[role=link],[tabindex]");
      if (!clickable) {
        clickable = element;
        for (let i = 0; i < 4 && clickable?.parentElement; i++, clickable = clickable.parentElement) {
          if (getComputedStyle(clickable).cursor === "pointer") break;
        }
      }
      if (clickable && visible(clickable)) return clickable;
    }
    return null;
  }
  async function waitFor(fn, timeout = 12000, step = 250) {
    const end = Date.now() + timeout;
    while (Date.now() < end) { const value = fn(); if (value) return value; await sleep(step); }
    throw new Error("等待网页控件超时");
  }
  function click(el) { el.scrollIntoView({block:"center", inline:"center"}); el.click(); }
  async function trustedInput(el, action = "click") {
    if (!trustedInputReady) {
      const warmup = await runtimeMessage({type:"trusted-input", action:"move", x:2, y:2});
      if (!warmup?.ok) throw new Error(`浏览器真实输入初始化失败：${warmup?.error || "未知错误"}`);
      trustedInputReady = true;
      await sleep(700);
    }
    if (el) el.scrollIntoView({block:"center", inline:"center"});
    await sleep(80);
    const rect = el?.getBoundingClientRect();
    const response = await runtimeMessage({type:"trusted-input", action,
      x:rect ? rect.left + rect.width / 2 : 0, y:rect ? rect.top + rect.height / 2 : 0});
    if (!response?.ok) throw new Error(`浏览器真实输入失败：${response?.error || "未知错误"}`);
  }

  async function enterImagine() {
    let lastError;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const imagine = await waitFor(() => exactText(["imagine"]), attempt ? 8000 : 5000, 200);
        if (attempt) await trustedInput(imagine, "click");
        else click(imagine);
        await sleep(attempt ? 650 : 900);
        const video = await waitFor(() => exactText(["视频", "video"]), attempt ? 8000 : 5000, 200);
        if (attempt) await trustedInput(video, "click");
        else click(video);
        await sleep(500);
        return;
      } catch (error) {
        lastError = error;
        await sleep(650);
      }
    }
    throw new Error(`返回 Imagine 视频制作页失败：${String(lastError?.message || lastError || "未知错误")}`);
  }
  function composerRoot() {
    const marker = exactText(["480p", "720p", "1080p"]);
    let root = marker;
    for (let i = 0; i < 8 && root; i++, root = root.parentElement) {
      if (root.querySelector?.('textarea,[contenteditable="true"],[role=textbox]') && root.querySelectorAll?.("button").length >= 3) return root;
    }
    const editor = all('textarea,[contenteditable="true"],[role=textbox]').find(el => !el.closest("nav,aside"));
    root = editor;
    for (let i = 0; i < 6 && root?.parentElement; i++, root = root.parentElement) {
      if (root.querySelectorAll?.("button").length >= 3) return root;
    }
    return editor?.parentElement || null;
  }
  async function upload(task) {
    const beforeImages = new Set(all("img"));
    const root = await waitFor(composerRoot);
    let input = [...root.querySelectorAll('input[type="file"]')].find(i => !i.accept || /image|png|jpeg|webp/i.test(i.accept));
    if (!input) {
      const add = [...root.querySelectorAll("button,[role=button]")].find(el => /添加图片|add image|上传图片|upload image|image/.test(textOf(el)));
      if (add) { click(add); await sleep(300); input = [...root.querySelectorAll('input[type="file"]')].find(i => !i.accept || /image/i.test(i.accept)); }
    }
    const response = await fetch(`${API}/image/${task.id}`);
    const blob = await response.blob();
    const file = new File([blob], task.name, {type: blob.type || "image/png"});
    const transfer = new DataTransfer(); transfer.items.add(file);
    if (input) {
      input.files = transfer.files;
      input.dispatchEvent(new Event("input", {bubbles:true, composed:true}));
      input.dispatchEvent(new Event("change", {bubbles:true, composed:true}));
    } else {
      for (const type of ["dragenter", "dragover", "drop"])
        root.dispatchEvent(new DragEvent(type, {bubbles:true, cancelable:true, dataTransfer:transfer}));
    }
    return waitFor(() => {
      const nearby = [...root.querySelectorAll("img")].find(img => !beforeImages.has(img) && img.naturalWidth);
      return nearby || all("img").find(img => !beforeImages.has(img) && img.naturalWidth);
    }, 20000);
  }
  async function chooseUsage(value, preview) {
    const map = {
      "首帧":["首帧","first frame"], "中间帧":["intermediate frame","中间帧"],
      "末帧":["末帧","last frame"], "循环":["循环","loop"], "参考":["参考","reference"]
    };
    if (!preview) throw new Error("找不到已上传图片的缩略图");
    function findMenu() {
      return all('[role="menu"],div').filter(el => {
        const text = textOf(el);
        const hits = [["首帧","first frame"],["intermediate frame","中间帧"],["末帧","last frame"],["循环","loop"],["参考","reference"]]
          .filter(names => names.some(name => text.includes(norm(name)))).length;
        const rect = el.getBoundingClientRect();
        return hits >= 3 && rect.width < 500 && rect.height < 600;
      }).sort((a,b) => a.getBoundingClientRect().width * a.getBoundingClientRect().height - b.getBoundingClientRect().width * b.getBoundingClientRect().height)[0];
    }
    await runtimeMessage({type:"trusted-input", action:"move", x:2, y:2});
    await sleep(120);
    await trustedInput(preview, "move");
    await sleep(700);
    let menu = findMenu();
    if (!menu) {
      const imageRect = preview.getBoundingClientRect();
      const nearby = all("button,[role=button]").filter(el => {
        const rect = el.getBoundingClientRect();
        return rect.left < imageRect.right + 180 && rect.right > imageRect.left - 20 &&
               rect.top < imageRect.bottom + 50 && rect.bottom > imageRect.top - 30;
      }).sort((a,b) => a.getBoundingClientRect().width * a.getBoundingClientRect().height - b.getBoundingClientRect().width * b.getBoundingClientRect().height);
      for (const control of nearby) {
        await trustedInput(control, "move"); await sleep(450);
        if ((menu = findMenu())) break;
      }
    }
    if (!menu) {
      await trustedInput(preview, "click"); await sleep(500);
      menu = findMenu();
    }
    if (!menu) throw new Error("悬停缩略图及其用途控件后仍未出现五项用途菜单");
    const item = await waitFor(() => exactText(map[value], menu), 3000);
    await trustedInput(item, "click"); await sleep(350);
  }
  function promptEditor() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll('textarea,[contenteditable="true"],[role=textbox]')].filter(visible).find(el => {
      const hint = norm(el.getAttribute("placeholder") || el.getAttribute("aria-label"));
      return !hint.includes("search") && !el.closest("nav");
    });
  }
  async function setPrompt(value) {
    const editor = await waitFor(promptEditor);
    editor.focus();
    if (editor.tagName === "TEXTAREA" || editor.tagName === "INPUT") {
      const setter = Object.getOwnPropertyDescriptor(editor.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value").set;
      setter.call(editor, value); editor.dispatchEvent(new Event("input", {bubbles:true}));
    } else {
      editor.textContent = "";
      document.execCommand("insertText", false, value);
      editor.dispatchEvent(new InputEvent("input", {bubbles:true, inputType:"insertText", data:value}));
    }
    await waitFor(() => norm(editor.value || editor.innerText || editor.textContent).includes(norm(value).slice(0, 12)), 3000);
  }
  async function chooseOption(currentLabels, desiredLabels) {
    const root = composerRoot() || document;
    const control = await waitFor(() => [...root.querySelectorAll("button,[role=button]")].filter(visible)
      .find(el => currentLabels.map(norm).some(label => textOf(el).includes(label))));
    const desired = desiredLabels.map(norm);
    const current = norm([
      control.innerText, control.textContent, control.getAttribute("aria-label"),
      control.getAttribute("aria-valuetext"), control.getAttribute("title")
    ].filter(Boolean).join(" "));
    // Resolution, duration and ratio controls expose their selected value in
    // the closed button. Leave the control untouched when it already matches.
    if (desired.some(label => current === label || current.includes(label))) return false;
    await trustedInput(control, "click"); await sleep(220);
    const option = await waitFor(() => exactText(desiredLabels)); await trustedInput(option, "click"); await sleep(250);
    return true;
  }
  let lastAudioState = null;
  function audioControl() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("button,[role=button]")].filter(visible).find(el => {
      const svg = el.querySelector("svg");
      const marker = norm([
        el.getAttribute("aria-label"), el.getAttribute("title"), el.getAttribute("data-testid"),
        el.getAttribute("data-tooltip-content"), svg?.getAttribute("aria-label"),
        svg?.getAttribute("data-icon"), svg?.getAttribute("class")
      ].filter(Boolean).join(" "));
      return /音频|声音|静音|audio|sound|mute|unmute|volume|speaker/.test(marker);
    });
  }
  function audioState(control) {
    for (const attribute of ["aria-pressed", "aria-checked"]) {
      const value = control.getAttribute(attribute);
      if (value === "true") return true;
      if (value === "false") return false;
    }
    const state = norm(control.getAttribute("data-state"));
    if (["on", "checked", "enabled", "active"].includes(state)) return true;
    if (["off", "unchecked", "disabled", "inactive"].includes(state)) return false;
    const svg = control.querySelector("svg");
    const marker = norm([
      control.getAttribute("aria-label"), control.getAttribute("title"),
      control.getAttribute("data-tooltip-content"), svg?.getAttribute("aria-label"),
      svg?.getAttribute("data-icon"), svg?.getAttribute("class")
    ].filter(Boolean).join(" "));
    if (/volume-x|volume-off|speaker-x|muted|静音|音频关闭|声音关闭/.test(marker)) return false;
    if (/volume-2|volume-high|speaker-wave|unmuted|取消静音|音频开启|声音开启/.test(marker)) return true;
    // Labels phrased as an action describe the state reached after clicking.
    if (/关闭音频|关闭声音|disable audio|turn off audio|mute audio/.test(marker)) return true;
    if (/开启音频|开启声音|enable audio|turn on audio|unmute audio/.test(marker)) return false;
    return null;
  }
  async function configureAudio(value) {
    const desired = value !== "关闭" && value !== false;
    const control = await waitFor(audioControl, 8000, 250);
    let current = audioState(control);
    // Grok starts new video composers with audio enabled. Remember subsequent
    // changes so a run never blindly toggles the control on every task.
    if (current === null) current = lastAudioState === null ? true : lastAudioState;
    if (current !== desired) {
      await trustedInput(control, "click");
      await sleep(300);
    }
    lastAudioState = desired;
  }
  async function waitUploadReady(preview) {
    await waitFor(() => preview?.isConnected && preview.complete && preview.naturalWidth > 0, 30000);
    const holder = preview.parentElement?.parentElement || preview.parentElement;
    await waitFor(() => !holder?.querySelector('[aria-busy="true"],[role="progressbar"],[data-state="loading"]'), 60000, 500);
    await sleep(1800);
  }
  function closestVideoRatio(preview) {
    const width = Number(preview?.naturalWidth || preview?.width || 0);
    const height = Number(preview?.naturalHeight || preview?.height || 0);
    return globalThis.grokClosestVideoRatio(width, height);
  }
  async function configure(task, preview) {
    const detectedRatio = closestVideoRatio(preview);
    const selectedRatio = ["自动", "由底图决定"].includes(task.ratio) ? detectedRatio : task.ratio;
    const selectedResolution = selectedRatio === "9:16" && task.resolution === "1080p" ? "720p" : task.resolution;
    await event(task.id, "正在设置", {detected_ratio:detectedRatio});
    await chooseUsage(task.usage, preview);
    await setPrompt(task.prompt);
    await chooseOption(["480p","720p","1080p"], [selectedResolution]);
    await chooseOption(["6s","10s","15s"], [task.duration]);
    if (task.usage !== "首帧" && task.ratio !== "由底图决定")
      await chooseOption(["自动模式","auto","2:3","3:2","1:1","9:16","16:9"], [selectedRatio]);
    await configureAudio(task.audio);
  }
  function generationCandidates() {
    return all('[data-testid*="generation"],[data-testid*="generating"],[data-state="generating"]');
  }
  function generationProgressSignal() {
    const pageText = norm(document.body?.innerText || document.body?.textContent);
    const textProgress = /(?:正在生成|生成中|处理中|generating|processing)(?:\s|：|:)*\d{0,3}\s*%?/.test(pageText);
    const progressElement = !!document.querySelector('[role="progressbar"],[aria-valuenow],[data-state="progress"],[data-state="generating"]');
    return textProgress || progressElement;
  }
  function rightmostButton(scope) {
    return [...scope.querySelectorAll("button")].filter(visible)
      .sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right)[0];
  }
  async function submit(task, preview) {
    await event(task.id, "正在提交");
    const before = new Set(generationCandidates());
    const beforeUrl = location.href;
    const editor = promptEditor();
    const scope = composerRoot() || editor?.closest("form") || document;
    let send;
    try {
      send = await waitFor(() => {
        const candidate = [...scope.querySelectorAll("button")].filter(visible)
          .find(b => /发送|生成|send|generate|submit/.test(textOf(b))) || scope.querySelector('button[type="submit"]') || rightmostButton(scope);
        return candidate && !candidate.disabled && candidate.getAttribute("aria-disabled") !== "true" ? candidate : null;
      }, 60000, 500);
    } catch (_) {
      throw new Error("图片上传完成后找不到可用的发送按钮");
    }
    // The configured interval is the minimum distance between two send
    // clicks. Uploading and configuring the next task may happen during that
    // interval, so do not idle here after the previous task was confirmed.
    const previousSubmitAt = Number(sessionStorage.getItem(LAST_SUBMIT_AT_KEY) || 0);
    const previousInterval = Math.max(0, Number(sessionStorage.getItem(LAST_SUBMIT_INTERVAL_KEY)) || 0);
    const remainingInterval = previousInterval - (Date.now() - previousSubmitAt);
    if (previousSubmitAt && remainingInterval > 0) await sleep(remainingInterval);
    sessionStorage.grokBatchSubmitting = JSON.stringify(task);
    sessionStorage.setItem(LAST_SUBMIT_AT_KEY, String(Date.now()));
    sessionStorage.setItem(LAST_SUBMIT_INTERVAL_KEY, String(Math.max(0, Number(task.submit_interval) || 0) * 1000));
    await trustedInput(send, "click");
    await sleep(600);
    try {
      await waitFor(() => {
        const added = generationCandidates().find(el => !before.has(el));
        const currentEditor = promptEditor();
        const cleared = !norm(currentEditor?.value || currentEditor?.innerText || currentEditor?.textContent);
        const composerReset = cleared && (!preview?.isConnected || !scope.contains(preview));
        const sendConsumed = !send.isConnected || send.disabled || send.getAttribute("aria-disabled") === "true";
        const navigatedToPost = location.href !== beforeUrl && /\/imagine\/post\//i.test(location.pathname);
        return navigatedToPost || added || generationProgressSignal() || composerReset || sendConsumed;
      }, 40000, 500);
    } catch (_) {
      throw new Error("已点击发送，但未检测到生成进度或页面切换");
    }
    await event(task.id, "已提交");
    delete sessionStorage.grokBatchSubmitting;
  }
  async function runTask(task) {
    let stage = "进入 Imagine";
    try {
      await enterImagine(); stage = "上传图片";
      const preview = await upload(task); stage = "等待图片上传完成";
      await waitUploadReady(preview); stage = "设置图片用途";
      await configure(task, preview); stage = "发送制作任务";
      await submit(task, preview);
    } catch (error) {
      if (stopped || !extensionAlive()) {
        if (!sessionStorage.grokBatchSubmitting)
          await event(task.id, "等待提交").catch(() => {});
        return;
      }
      delete sessionStorage.grokBatchSubmitting;
      await event(task.id, "提交失败", {error:`${stage}：${String(error.message || error)}`}).catch(() => {});
    }
  }
  async function recoverNavigatedSubmission(task) {
    try {
      await waitFor(() => /\/imagine\/post\//i.test(location.pathname) || generationProgressSignal() || all("video").length, 20000);
      await event(task.id, "已提交");
      delete sessionStorage.grokBatchSubmitting;
    } catch (error) {
      delete sessionStorage.grokBatchSubmitting;
      await event(task.id, "需要人工处理", {error:"提交后页面发生跳转，但未能自动确认状态，请检查 Grok 页面。"}).catch(()=>{});
    }
  }
  async function tick() {
    if (stopped || !extensionAlive()) { stopped = true; return; }
    let nextDelay = 1200;
    try {
      if (desktopOnline && controller && automationActive && !busy) {
        const interruptedSubmit = sessionStorage.grokBatchSubmitting && JSON.parse(sessionStorage.grokBatchSubmitting);
        if (interruptedSubmit) {
          busy = true; await recoverNavigatedSubmission(interruptedSubmit); busy = false;
          scheduleTick(100); return;
        }
        const task = await api("/next");
        if (task.id) { busy = true; await runTask(task); busy = false; nextDelay = 400; }
      }
    } catch (_) { /* Desktop app is closed or page is navigating. */ }
    scheduleTick(nextDelay);
  }
  function scheduleTick(delay = 0) {
    if (stopped || tickTimer || busy || !desktopOnline || !controller || !automationActive) return;
    tickTimer = setTimeout(() => { tickTimer = 0; tick(); }, delay);
  }
  async function heartbeat() {
    if (stopped || !extensionAlive()) { stopped = true; return; }
    try {
      const state = await api("/heartbeat", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({client:CLIENT, version:HELPER_VERSION, browser:BROWSER_NAME})});
      if (state.required_version && state.required_version !== HELPER_VERSION) {
        desktopOnline = true;
        versionMismatch = true;
        controller = false;
        automationActive = false;
        trustedInputReady = false;
        if (tickTimer) { clearTimeout(tickTimer); tickTimer = 0; }
        if (reportedIconState !== "warning") {
          await runtimeMessage({type:"desktop-status", state:"warning", requiredVersion:state.required_version}).catch(() => {});
          reportedIconState = "warning";
        }
        return;
      }
      versionMismatch = false;
      desktopOnline = true;
      const helperState = state.helper_state || (state.running ? "running" : "ready");
      if (reportedIconState !== helperState) {
        await runtimeMessage({type:"desktop-status", state:helperState}).catch(() => {});
        reportedIconState = helperState;
      }
      offlineNotified = false;
      controller = !!state.active;
      const wasActive = automationActive;
      automationActive = !!state.running;
      if (automationActive) scheduleTick();
      else {
        if (tickTimer) { clearTimeout(tickTimer); tickTimer = 0; }
        if (wasActive) await runtimeMessage({type:"automation-idle"}).catch(() => {});
      }
    } catch (_) {
      desktopOnline = false;
      versionMismatch = false;
      controller = false;
      automationActive = false;
      trustedInputReady = false;
      if (tickTimer) { clearTimeout(tickTimer); tickTimer = 0; }
      if (!offlineNotified) {
        offlineNotified = true;
        await runtimeMessage({type:"desktop-offline"}).catch(() => {});
      }
      reportedIconState = "offline";
    }
  }
  async function heartbeatLoop() {
    await heartbeat();
    if (!stopped) setTimeout(heartbeatLoop, versionMismatch ? 5000 : (desktopOnline ? (automationActive ? 1500 : 3000) : 5000));
  }
  heartbeatLoop();
  document.addEventListener("visibilitychange", () => {
    if (!stopped && desktopOnline && automationActive && busy)
      runtimeMessage({type:"keep-active"}).catch(() => {});
  });
  setInterval(() => {
    if (!stopped && desktopOnline && automationActive && busy)
      runtimeMessage({type:"keep-active"}).catch(() => {});
  }, 8000);
})();
