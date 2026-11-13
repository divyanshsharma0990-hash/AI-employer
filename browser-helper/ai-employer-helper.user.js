// ==UserScript==
// @name         AI Employer helper
// @description  Two buttons on AI websites: "Paste prompt" fills the chat box from your clipboard, "Copy reply" copies the latest answer. You still press Send yourself.
// @match        https://chatgpt.com/*
// @match        https://chat.openai.com/*
// @match        https://claude.ai/*
// @match        https://gemini.google.com/*
// @grant        none
// ==/UserScript==
(function () {
  "use strict";

  // Websites change their layout now and then. If a button stops working, add the new selector here.
  const INPUTS = [
    "#prompt-textarea",
    "rich-textarea div[contenteditable='true']",
    "div.ProseMirror[contenteditable='true']",
    "div[contenteditable='true']",
    "textarea",
  ];
  const REPLIES = [
    "[data-message-author-role='assistant']",
    ".font-claude-response",
    ".font-claude-message",
    "message-content",
  ];

  function toast(msg) {
    const t = document.createElement("div");
    t.textContent = msg;
    t.style.cssText =
      "position:fixed;bottom:76px;right:16px;z-index:2147483647;background:#0D1524;color:#F4F7FB;" +
      "padding:10px 14px;border-radius:8px;border:1px solid #FFC857;font:14px/1.4 system-ui,sans-serif;max-width:280px";
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3500);
  }

  function findInput() {
    for (const s of INPUTS) {
      const el = document.querySelector(s);
      if (el) return el;
    }
    return null;
  }

  async function pastePrompt() {
    let text = "";
    try {
      text = await navigator.clipboard.readText();
    } catch (e) {
      toast("Allow clipboard access when the browser asks, then press the button again.");
      return;
    }
    if (!text.trim()) return toast("Your clipboard is empty. Copy the prompt in AI Employer first.");
    const box = findInput();
    if (!box) return toast("Could not find the chat box on this page.");
    box.focus();
    if (box.tagName === "TEXTAREA") {
      box.value = text;
      box.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      document.execCommand("selectAll", false, null);
      document.execCommand("insertText", false, text);
    }
    toast("Prompt pasted. Check it, then press Send.");
  }

  async function copyReply() {
    let text = "";
    for (const s of REPLIES) {
      const found = document.querySelectorAll(s);
      if (found.length) {
        text = found[found.length - 1].innerText.trim();
        break;
      }
    }
    if (!text) text = String(window.getSelection()).trim(); // fallback: whatever you selected
    if (!text) return toast("Could not find the reply. Select the text and press again.");
    try {
      await navigator.clipboard.writeText(text);
      toast(`Copied ${text.length} characters. Paste it in AI Employer.`);
    } catch (e) {
      toast("The browser blocked copying. Click on the page once and try again.");
    }
  }

  function button(label, onClick) {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = onClick;
    b.style.cssText =
      "background:#FFC857;color:#0D1524;border:0;border-radius:8px;padding:9px 12px;font:600 14px system-ui,sans-serif;cursor:pointer";
    return b;
  }

  const bar = document.createElement("div");
  bar.style.cssText = "position:fixed;bottom:16px;right:16px;z-index:2147483647;display:flex;gap:8px";
  bar.append(button("Paste prompt", pastePrompt), button("Copy reply", copyReply));
  document.body.appendChild(bar);
})();
