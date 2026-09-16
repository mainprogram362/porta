"use strict";
let nativePort = null;
let connectionId = null;
let status = "未接続。接続ボタンを押してください。";
const locks = new Map();
const active = new Set();
const supported = url => typeof url === "string" && /^https?:\/\//.test(url);

function disconnect() {
  locks.clear();
  connectionId = null;
  if (nativePort) { const old = nativePort; nativePort = null; old.disconnect(); }
  status = "切断しました。次の操作は送出しません。送出済みの操作は取り消せません。";
}
function connect() {
  if (nativePort) return;
  connectionId = crypto.randomUUID();
  const port = browser.runtime.connectNative("porta_browser");
  nativePort = port;
  status = "接続要求済み。PORTAの候補更新で確認してください。";
  port.onDisconnect.addListener(() => {
    if (nativePort === port) {
      nativePort = null; connectionId = null; locks.clear();
      status = "切断: " + (port.error?.message || "Native Hostを確認してください。");
    }
  });
  port.onMessage.addListener(async message => {
    let response;
    try { response = {ok: true, result: await dispatch(message.command)}; }
    catch (error) { response = {ok: false, error: String(error.message || error)}; }
    try { port.postMessage({id: message.id, ...response}); } catch (_) { /* no replay */ }
  });
}
browser.runtime.onMessage.addListener((message, sender) => {
  // Only the extension popup may control the native connection.
  if (sender.tab || !sender.url?.startsWith(browser.runtime.getURL(""))) return undefined;
  if (message.op === "connect") connect();
  if (message.op === "disconnect") disconnect();
  return Promise.resolve({status});
});

async function checkedTab(target) {
  if (!nativePort || target.connectionId !== connectionId) throw new Error("Firefox接続が変わりました。選び直してください。");
  const tab = await browser.tabs.get(target.tabId);
  if (!nativePort || target.connectionId !== connectionId) throw new Error("Firefox接続が変更されました。");
  if (tab.windowId !== target.windowId) throw new Error("タブが別ウィンドウへ移動しました。選び直してください。");
  if (!supported(tab.url)) throw new Error("このページは操作できません。http/httpsページを選んでください。");
  return tab;
}
async function content(target, command) {
  await checkedTab(target);
  if (command.deadline && Date.now() >= command.deadline) throw new Error("命令の期限を超えました。再送しません。");
  const reply = await browser.tabs.sendMessage(target.tabId, {channel: "porta", ...command}, {frameId: 0});
  if (!reply?.ok) throw new Error(reply?.error || "ページ応答がありません。");
  return reply.result;
}
async function snapshot(target) {
  const tab = await checkedTab(target);
  const pattern = new URL(tab.url).origin + "/*";
  if (!await browser.permissions.contains({origins: [pattern]}))
    throw new Error("Firefoxの拡張機能ボタンで、このサイトの操作を許可してください。");
  await browser.tabs.executeScript(target.tabId, {file: "content.js", frameId: 0});
  return content(target, {op: "snapshot"});
}
function owned(command) {
  const lease = locks.get(command.target.tabId);
  if (!lease || lease.runId !== command.runId) throw new Error("作業の操作権がありません。再準備してください。");
  if (Date.now() > lease.deadline) { locks.delete(command.target.tabId); throw new Error("作業の制限時間を超えました。"); }
  return lease;
}
async function dispatch(command) {
  if (!command || Date.now() >= command.deadline) throw new Error("命令の期限を超えました。");
  if (command.op === "inventory") {
    const saved = await browser.storage.local.get(["label", "profileId"]);
    if (!saved.profileId) { saved.profileId = crypto.randomUUID(); await browser.storage.local.set({profileId: saved.profileId}); }
    const windows = await browser.windows.getAll({populate: true, windowTypes: ["normal", "popup"]});
    return {connectionId, profileId: saved.profileId, label: saved.label || "Firefox", windows: windows.map(win => ({
      id: win.id, focused: win.focused, incognito: win.incognito, state: win.state,
      tabs: win.tabs.map(tab => ({id: tab.id, windowId: win.id, index: tab.index,
        title: tab.title || "", url: tab.url || "", active: tab.active, pinned: tab.pinned,
        discarded: tab.discarded, supported: supported(tab.url)}))}))};
  }
  if (command.op === "end") {
    if (command.target.connectionId !== connectionId) throw new Error("Firefox接続が変わりました。");
    owned(command); locks.delete(command.target.tabId); return {state: "操作権を解除しました"};
  }
  await checkedTab(command.target);
  if (command.op === "focus") {
    await browser.windows.update(command.target.windowId, {focused: true});
    await browser.tabs.update(command.target.tabId, {active: true});
    return {state: "前面表示しました"};
  }
  if (command.op === "begin") {
    const existing = locks.get(command.target.tabId);
    if (existing && Date.now() <= existing.deadline) throw new Error("このタブは別の作業で使用中です。");
    const lease = {runId: command.runId, deadline: Date.now() + 600000, origins: command.origins, clicks: false};
    locks.set(command.target.tabId, lease);
    try {
      const page = await snapshot(command.target);
      if (page.url !== command.url) throw new Error("選択時とURLが違います。候補を更新してください。");
      lease.documentId = page.documentId; lease.url = page.url;
      return page;
    } catch (error) { locks.delete(command.target.tabId); throw error; }
  }
  if (!["inspect", "step"].includes(command.op)) throw new Error("不明な命令です。");
  const lease = owned(command);
  const step = command.step;
  if (!step || !lease.origins.includes(new URL(step.url).origin)) throw new Error("許可していないサイトです。");
  if (active.has(command.target.tabId)) throw new Error("このタブへの命令が実行中です。");
  active.add(command.target.tabId);
  try {
    if (step.action === "wait_page") {
      if (!lease.clicks) throw new Error("ページ遷移待ちはクリック直後だけ可能です。");
      const deadline = Date.now() + Math.min(step.timeout || 10, 30) * 1000;
      while (Date.now() < deadline) {
        owned(command);
        const tab = await checkedTab(command.target);
        if (!lease.origins.includes(new URL(tab.url).origin)) throw new Error("許可していないサイトに移動しました。");
        if (tab.url === step.url && tab.status === "complete") {
          const page = await snapshot(command.target);
          if (page.url !== step.url) throw new Error("ページが変更されました。");
          if (page.documentId !== lease.documentId || page.url !== lease.url) {
            owned(command);
            lease.documentId = page.documentId; lease.url = page.url; lease.clicks = false;
            return {...page, state: "指定ページを確認しました"};
          }
        }
        await new Promise(resolve => setTimeout(resolve, 200));
      }
      throw new Error("指定ページへの遷移を確認できません。結果不明です。クリックを再送しません。");
    }
    if (step.url !== lease.url) throw new Error("URLが前の手順と異なります。遷移確認が必要です。");
    owned(command);
    const result = await content(command.target, {op: command.op === "inspect" ? "inspect" : "step",
      deadline: command.deadline, documentId: lease.documentId, url: step.url,
      step: {...step, limit: step.limit || 100, attribute: step.attribute || "text"}});
    lease.clicks = command.op !== "inspect" && step.action === "click";
    return result;
  } finally { active.delete(command.target.tabId); }
}
