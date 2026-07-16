#!/usr/bin/env node
// THROWAWAY PROTOTYPE ONLY: observe the disposable renderer over loopback CDP.

import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";

const DISCOVERY_REQUEST_TIMEOUT_MS = 2000;
const WEBSOCKET_OPEN_TIMEOUT_MS = 5000;

export async function fetchTargets(base, timeoutMs = DISCOVERY_REQUEST_TIMEOUT_MS, fetchImpl = fetch) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`${base}/json/list`, { signal: controller.signal });
    if (!response.ok) throw new Error(`CDP target list returned ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

export function openWebSocket(
  url,
  timeoutMs = WEBSOCKET_OPEN_TIMEOUT_MS,
  WebSocketConstructor = globalThis.WebSocket,
) {
  const ws = new WebSocketConstructor(url);
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      ws.close();
      settle(reject, new Error(`CDP WebSocket did not open within ${timeoutMs}ms`));
    }, timeoutMs);
    const settle = (callback, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      callback(value);
    };
    ws.addEventListener("open", () => settle(resolve, ws), { once: true });
    ws.addEventListener("error", () => settle(reject, new Error("CDP WebSocket failed to open")), {
      once: true,
    });
  });
}

function emit(kind, data) {
  process.stdout.write(`${JSON.stringify({ at: new Date().toISOString(), kind, ...data })}\n`);
}

export function summarizeText(value) {
  const text = typeof value === "string" ? value : JSON.stringify(value) ?? String(value);
  return {
    length: text.length,
    sha256: createHash("sha256").update(text).digest("hex"),
  };
}

function summarizeControls(controls) {
  const values = Array.isArray(controls) ? controls : [];
  return {
    count: values.length,
    disabledCount: values.filter((control) => control?.disabled === true).length,
    sha256: summarizeText(values).sha256,
  };
}

function redactRendererState(value) {
  const state = value && typeof value === "object" ? value : {};
  const readyState = ["loading", "interactive", "complete"].includes(state.readyState)
    ? state.readyState
    : "other";
  return {
    url: summarizeText(state.url),
    title: summarizeText(state.title),
    document: summarizeText(state.text),
    readyState,
    mainUi: state.mainUi === true,
    loginWall: state.loginWall === true,
    controls: summarizeControls(state.controls),
    likelyBridgeGlobals: summarizeText(state.likelyBridgeGlobals),
    electronBridgeShape: state.electronBridgeShape === null
      ? null
      : summarizeText(state.electronBridgeShape),
  };
}

export function redactCdpMessage(message) {
  if (message.method === "Network.requestWillBeSent") {
    const { request = {}, type } = message.params ?? {};
    return {
      kind: "request",
      data: { method: request.method, resourceType: type, url: summarizeText(request.url) },
    };
  }
  if (message.method === "Network.loadingFailed") {
    const params = message.params ?? {};
    return {
      kind: "request-failed",
      data: {
        blockedReason: params.blockedReason,
        errorText: summarizeText(params.errorText),
        type: params.type,
      },
    };
  }
  if (message.method === "Runtime.consoleAPICalled") {
    const params = message.params ?? {};
    return {
      kind: "console",
      data: {
        level: params.type,
        values: (params.args ?? []).map((arg) => summarizeText(arg.value ?? arg.description ?? arg.type)),
      },
    };
  }
  if (message.id && message.result?.result?.value) {
    return { kind: "renderer-state", data: redactRendererState(message.result.result.value) };
  }
  return null;
}

async function main() {
  const port = Number(process.argv[2]);
  const durationMs = Number(process.argv[3] ?? 20000);
  const base = `http://127.0.0.1:${port}`;
  const started = Date.now();

  let target;
  while (Date.now() - started < 20000) {
    const remainingMs = 20000 - (Date.now() - started);
    try {
      const candidates = await fetchTargets(
        base,
        Math.min(DISCOVERY_REQUEST_TIMEOUT_MS, remainingMs),
      );
      target = candidates.find((candidate) => candidate.type === "page");
      if (target) break;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  if (!target) {
    emit("observer-error", { message: "No renderer CDP target appeared within 20 seconds" });
    process.exit(2);
  }

  emit("target", {
    type: target.type,
    title: summarizeText(target.title),
    url: summarizeText(target.url),
  });
  const ws = await openWebSocket(target.webSocketDebuggerUrl);
  let nextId = 1;

  function send(method, params = {}) {
    ws.send(JSON.stringify({ id: nextId++, method, params }));
  }

  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    const evidence = redactCdpMessage(message);
    if (evidence) emit(evidence.kind, evidence.data);
  });

  send("Network.enable");
  send("Runtime.enable");

  const stateExpression = `({
    url: location.href,
    title: document.title,
    text: (document.body?.innerText ?? "").replace(/\\s+/g, " ").trim().slice(0, 4000),
    loginWall: (document.body?.innerText ?? "").includes("Sign in to ChatGPT"),
    readyState: document.readyState,
    mainUi: Boolean(
      [...document.querySelectorAll("button")].some((element) => element.innerText?.includes("New task")) &&
      [...document.querySelectorAll("button")].some((element) => element.getAttribute("aria-label") === "Choose project") &&
      [...document.querySelectorAll("button")].some((element) => element.getAttribute("aria-label") === "Open settings") &&
      !(document.body?.innerText ?? "").includes("What type of work do you do?")
    ),
    controls: [...document.querySelectorAll("button, textarea, input, [contenteditable=true], [role=button]")]
      .slice(0, 80)
      .map((element) => ({
        tag: element.tagName,
        role: element.getAttribute("role"),
        type: element.getAttribute("type"),
        ariaLabel: element.getAttribute("aria-label"),
        placeholder: element.getAttribute("placeholder"),
        text: (element.innerText || element.value || "").replace(/\\s+/g, " ").trim().slice(0, 160),
        disabled: Boolean(element.disabled),
      })),
    likelyBridgeGlobals: Object.keys(window)
      .filter((key) => /codex|electron|desktop|bridge|rpc|api/i.test(key))
      .sort()
      .slice(0, 80),
    electronBridgeShape: window.electronBridge ? Object.fromEntries(
      Object.entries(window.electronBridge)
        .slice(0, 80)
        .map(([key, value]) => [key, typeof value === "object" && value !== null ? Object.keys(value).slice(0, 80) : typeof value])
    ) : null,
  })`;
  send("Runtime.evaluate", { expression: stateExpression, returnByValue: true });
  const interval = setInterval(() => {
    send("Runtime.evaluate", { expression: stateExpression, returnByValue: true });
  }, 2500);
  const onboardingInterval = setInterval(() => {
    send("Runtime.evaluate", {
      expression: `(() => {
        const button = [...document.querySelectorAll("button")]
          .find((candidate) => candidate.innerText?.trim() === "Skip" && !candidate.disabled);
        if (!button) return { action: "skip-onboarding", clicked: false };
        button.click();
        return { action: "skip-onboarding", clicked: true };
      })()`,
      returnByValue: true,
    });
  }, 3000);

  await new Promise((resolve) => setTimeout(resolve, durationMs));
  clearInterval(interval);
  clearInterval(onboardingInterval);
  ws.close();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
