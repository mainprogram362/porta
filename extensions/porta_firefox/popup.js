"use strict";
let originPattern = null;
const status = document.getElementById("status");
async function initialize() {
  const saved = await browser.storage.local.get("label");
  document.getElementById("label").value = saved.label || "Firefox";
  const [tab] = await browser.tabs.query({active: true, currentWindow: true});
  if (tab && /^https?:/.test(tab.url || "")) originPattern = new URL(tab.url).origin + "/*";
  document.getElementById("grant").disabled = !originPattern;
  status.textContent = (await browser.runtime.sendMessage({op: "status"})).status;
}
document.getElementById("connect").onclick = async () => {
  try {
    await browser.storage.local.set({label: document.getElementById("label").value.trim() || "Firefox"});
    status.textContent = (await browser.runtime.sendMessage({op: "connect"})).status;
  } catch (error) { status.textContent = String(error); }
};
document.getElementById("disconnect").onclick = async () => {
  status.textContent = (await browser.runtime.sendMessage({op: "disconnect"})).status;
};
document.getElementById("grant").onclick = () => {
  // Request directly from the click handler to preserve the user gesture.
  browser.permissions.request({origins: [originPattern]}).then(
    granted => { status.textContent = granted ? "このサイトを許可しました。PORTAで対象を確認してください。" : "許可されませんでした。"; },
    error => { status.textContent = String(error); }
  );
};
initialize().catch(error => { status.textContent = String(error); });
