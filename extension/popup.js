const status = document.getElementById("status");
const statusText = document.getElementById("statusText");
const detail = document.getElementById("detail");
const manifest = chrome.runtime.getManifest();
document.getElementById("version").textContent = manifest.version_name;
function compareVersions(first, second) {
  const left = String(first || "").split(".").map(part => Number.parseInt(part, 10) || 0);
  const right = String(second || "").split(".").map(part => Number.parseInt(part, 10) || 0);
  for (let index = 0; index < Math.max(left.length, right.length); index++) {
    const difference = (left[index] || 0) - (right[index] || 0);
    if (difference) return difference > 0 ? 1 : -1;
  }
  return 0;
}
const controller = new AbortController();
setTimeout(() => controller.abort(), 1200);
fetch("http://127.0.0.1:17843/api/helper-version", {cache:"no-store", signal:controller.signal})
  .then(response => { if (!response.ok) throw new Error(); return response.json(); })
  .then(state => {
    if (state.version && state.version !== manifest.version) {
      status.classList.add("warning");
      statusText.textContent = "版本不一致 · 自动化已暂停";
      detail.textContent = compareVersions(state.version, manifest.version) < 0
        ? `检测到旧版桌面软件 ${state.version}，浏览器助手不会从 ${manifest.version} 自动降级。请关闭旧版软件并只运行最新版。`
        : `桌面软件 ${state.version}，浏览器助手 ${manifest.version}。正在尝试一次受控更新；如仍未升级，请在扩展管理页手动重新加载一次。`;
      return;
    }
    const helperState = state.helper_state || (state.running ? "running" : "ready");
    status.classList.add(helperState);
    if (helperState === "error") {
      statusText.textContent = "有任务需要处理";
      detail.textContent = "请返回桌面软件查看失败原因并决定是否重试。";
    } else if (helperState === "warning") {
      statusText.textContent = "任务已暂停";
      detail.textContent = "桌面软件中仍有等待任务，可在确认后继续。";
    } else if (helperState === "running") {
      statusText.textContent = "桌面软件已连接 · 正在运行任务";
      detail.textContent = "浏览器助手正在配合桌面软件处理任务。";
    } else {
      statusText.textContent = "桌面软件已连接 · 等待任务";
      detail.textContent = "只有开始批量任务后，助手才会操作 Grok 页面。";
    }
  })
  .catch(() => {
    statusText.textContent = "休眠中 · 桌面软件未打开";
    detail.textContent = "当前不会轮询任务、保持页面活动或连接调试器。";
  });
