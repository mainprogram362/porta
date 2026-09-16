/* Installed only in a selected top-level document on a permitted website. */
(() => {
  "use strict";
  if (globalThis.__portaAgent) return;
  globalThis.__portaAgent = true;
  // getRandomValues also works on ordinary HTTP pages.
  const documentId = Array.from(crypto.getRandomValues(new Uint8Array(16)),
    byte => byte.toString(16).padStart(2, "0")).join("");
  function snapshot() { return {documentId, url: location.href, title: document.title}; }
  function matching(step) {
    const found = Array.from(document.querySelectorAll(step.selector)).filter(element =>
      step.text === undefined || (element.textContent || "").trim() === step.text);
    if (!found.length) throw new Error("対象が0件です。ページの変更または未読込を確認してください。");
    if (!(step.action === "extract" && step.many === true) && found.length !== 1)
      throw new Error(`対象が${found.length}件あります。1件に特定してください。`);
    return found;
  }
  browser.runtime.onMessage.addListener(message => {
    if (message.channel !== "porta") return undefined;
    try {
      if (message.deadline && Date.now() >= message.deadline) throw new Error("命令の期限を超えました。操作しません。");
      if (message.op === "snapshot") return Promise.resolve({ok: true, result: snapshot()});
      if (message.documentId !== documentId || message.url !== location.href)
        throw new Error("選択後にページが変更されました。操作を止めました。");
      const step = message.step;
      const elements = matching(step);
      if (message.op === "inspect") {
        return Promise.resolve({ok: true, result: {...snapshot(), count: elements.length,
          elements: elements.slice(0, 20).map(element => ({tag: element.tagName,
            id: element.id, name: element.getAttribute("name"), type: element.getAttribute("type"),
            label: element.getAttribute("aria-label"), text: (element.textContent || "").trim().slice(0, 300)}))}});
      }
      if (message.op !== "step") throw new Error("不明な命令です。");
      let values = [];
      if (step.action === "fill" || step.action === "click") {
        const element = elements[0];
        if (!element.getClientRects().length || getComputedStyle(element).visibility !== "visible" ||
            element.matches(":disabled") || element.closest("[inert]"))
          throw new Error("対象が非表示または操作不可です。");
        if (step.action === "fill") {
          if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) ||
              element.readOnly || (element instanceof HTMLInputElement &&
                !["text", "search", "email", "url", "tel", "number", "password"].includes(element.type)))
            throw new Error("通常の編集可能な入力欄ではありません。");
          const prototype = element instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
          Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, step.value);
          element.dispatchEvent(new Event("input", {bubbles: true}));
          element.dispatchEvent(new Event("change", {bubbles: true}));
          if (element.value !== step.value) throw new Error("入力値が一致しません。入力済みの可能性があります。");
        } else {
          element.click();
        }
      } else if (step.action === "extract") {
        if (elements.length > step.limit) throw new Error(`抽出件数${elements.length}が上限${step.limit}を超えました。`);
        values = elements.map(element => {
          if (step.attribute === "value") return String(element.value ?? "");
          if (step.attribute === "href" || step.attribute === "src") return String(element[step.attribute] ?? element.getAttribute(step.attribute) ?? "");
          return (element.textContent || "").trim();
        });
        if (JSON.stringify(values).length > 100000) throw new Error("抽出結果が100000文字を超えました。対象を絞ってください。");
      } else if (step.action !== "check") throw new Error("未対応の手順です。");
      return Promise.resolve({ok: true, result: {...snapshot(), count: elements.length, values,
        state: step.action === "click" ? "クリック送出済み（サイト側の完了は未確認）" : "完了"}});
    } catch (error) {
      return Promise.resolve({ok: false, error: String(error.message || error)});
    }
  });
})();
