#!/usr/bin/env node
// THROWAWAY PROTOTYPE ONLY: observe the disposable renderer over loopback CDP.

const port = Number(process.argv[2]);
const durationMs = Number(process.argv[3] ?? 20000);
const base = `http://127.0.0.1:${port}`;
const started = Date.now();

function emit(kind, data) {
  process.stdout.write(`${JSON.stringify({ at: new Date().toISOString(), kind, ...data })}\n`);
}

async function targets() {
  const response = await fetch(`${base}/json/list`);
  if (!response.ok) throw new Error(`CDP target list returned ${response.status}`);
  return response.json();
}

let target;
while (Date.now() - started < 20000) {
  try {
    const candidates = await targets();
    target = candidates.find((candidate) => candidate.type === "page");
    if (target) break;
  } catch {}
  await new Promise((resolve) => setTimeout(resolve, 250));
}

if (!target) {
  emit("observer-error", { message: "No renderer CDP target appeared within 20 seconds" });
  process.exit(2);
}

emit("target", { id: target.id, title: target.title, url: target.url });
const ws = new WebSocket(target.webSocketDebuggerUrl);
let nextId = 1;

function send(method, params = {}) {
  ws.send(JSON.stringify({ id: nextId++, method, params }));
}

ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.method === "Network.requestWillBeSent") {
    const { request, type } = message.params;
    emit("request", { method: request.method, resourceType: type, url: request.url });
  } else if (message.method === "Network.loadingFailed") {
    emit("request-failed", {
      blockedReason: message.params.blockedReason,
      errorText: message.params.errorText,
      type: message.params.type,
    });
  } else if (message.method === "Runtime.consoleAPICalled") {
    emit("console", {
      level: message.params.type,
      values: message.params.args.map((arg) => arg.value ?? arg.description ?? arg.type),
    });
  } else if (message.id && message.result?.result?.value) {
    emit("renderer-state", message.result.result.value);
  }
});

await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve, { once: true });
  ws.addEventListener("error", reject, { once: true });
});
send("Network.enable");
send("Runtime.enable");

const stateExpression = `({
  url: location.href,
  title: document.title,
  text: (document.body?.innerText ?? "").replace(/\\s+/g, " ").trim().slice(0, 4000),
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
