#!/usr/bin/env node
// THROWAWAY PROTOTYPE ONLY: drive renderer-only ticket 12 checks over loopback CDP.

import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

const port = Number(process.argv[2]);
const phase = process.argv[3] ?? "first";
const timeoutMs = Number(process.argv[4] ?? 120000);
const phaseArgument = process.argv[5];
const base = `http://127.0.0.1:${port}`;
const started = Date.now();
const firstPrompt = "Reply exactly COLD_PHASE_ONE_OK and nothing else. Do not use tools.";
const firstSentinel = "COLD_PHASE_ONE_OK";
const secondPrompt = "Reply exactly COLD_PHASE_TWO_OK and nothing else. Do not use tools.";
const secondSentinel = "COLD_PHASE_TWO_OK";
const defaultModePrompt = "Confirm Default mode in one short sentence. Do not use tools.";
const planModePrompt = "Confirm Plan mode in one short sentence. Do not use tools.";
const cdpCommandTimeoutMs = Math.max(1000, Math.min(timeoutMs, 15000));

function emit(kind, data) {
  process.stdout.write(`${JSON.stringify({ at: new Date().toISOString(), kind, phase, ...data })}\n`);
}

function requestUrlEvidence(value) {
  if (typeof value !== "string") return { scheme: null, host: null };
  try {
    const parsed = new URL(value);
    return { scheme: parsed.protocol.slice(0, -1), host: parsed.host || null };
  } catch {
    return { scheme: null, host: null };
  }
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function expectRejected(action, expectedMessage) {
  try {
    await action();
  } catch (error) {
    if (error instanceof Error && error.message.includes(expectedMessage)) return;
    throw error;
  }
  throw new Error(`expected rejection containing ${expectedMessage}`);
}

function normalizedTextSha256(value) {
  return createHash("sha256").update(value.replace(/\s+/g, " ").trim()).digest("hex");
}

function textSha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function stripTrailingRendererTimestamp(value) {
  const match = value.match(/(?:^|\s+)((?:0?[1-9]|1[0-2]):[0-5]\d (?:AM|PM))\s*$/);
  if (!match) return { text: value, timestampRemoved: false, timestamp: null };
  return {
    text: value.slice(0, match.index).replace(/\s+$/g, ""),
    timestampRemoved: true,
    timestamp: match[1],
  };
}

function sentinelTextVerdict(text, expectedSentinel) {
  const trimmedText = text.trim();
  const exactMatch = trimmedText === expectedSentinel;
  return {
    matched: exactMatch,
    exactMatch,
    trimmedTextLength: trimmedText.length,
  };
}

function defaultModeMenuVerdict(text) {
  return text.includes("Plan mode") && text.includes("Turn plan mode on");
}

function planModeIndicatorVerdict(controls) {
  return controls.some((control) =>
    control.ariaLabel === "Plan" && control.text.trim() === "Plan"
  );
}

function projectSelectionVerdict(control, expectedFixtureRoot) {
  const expectedName = expectedFixtureRoot.split("/").filter(Boolean).at(-1) ?? "";
  const visibleText = [control?.text, control?.title, control?.ariaDescription]
    .filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  const escapedName = expectedName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const boundedName = new RegExp(
    `(^|[^\\p{L}\\p{N}._-])${escapedName}($|[^\\p{L}\\p{N}._-])`, "u"
  );
  const matched = Boolean(expectedName) && control?.count === 1 &&
    boundedName.test(visibleText);
  return { matched, expectedName, visibleTextLength: visibleText.length };
}

function uniqueVisibleControlVerdict(candidates) {
  if (candidates.length === 0) return { status: "pending", reason: "missing", count: 0 };
  if (candidates.length !== 1) {
    return { status: "duplicate", reason: "duplicate", count: candidates.length };
  }
  const candidate = candidates[0];
  if (candidate.disabled) return { status: "disabled", reason: "disabled", count: 1 };
  if (!candidate.visible) return { status: "pending", reason: "not-visible", count: 1 };
  return { status: "ready", count: 1, ...candidate };
}

function matchingControlVerdict(candidates, acceptedText) {
  const matches = candidates.filter((candidate) => acceptedText.includes(candidate.text));
  if (matches.length === 0) return { status: "pending", reason: "missing", count: 0 };
  if (matches.length !== 1) {
    return { status: "duplicate", reason: "duplicate", count: matches.length };
  }
  const candidate = matches[0];
  if (candidate.disabled) return { status: "disabled", reason: "disabled", count: 1 };
  if (!candidate.visible) return { status: "pending", reason: "not-visible", count: 1 };
  return { status: "ready", count: 1, ...candidate };
}

function worktreeCommandVerdict(candidates) {
  return matchingControlVerdict(candidates, [
    "New worktree",
    "New worktree Run this chat in a new worktree",
  ]);
}

function worktreeModeVerdict(candidates) {
  return matchingControlVerdict(candidates, ["New worktree", "Worktree"]);
}

function matchingControlExpression(candidatesExpression, acceptedText) {
  return `(${matchingControlVerdict.toString()})(${candidatesExpression}, ${JSON.stringify(acceptedText)})`;
}

async function runSelfTests() {
  const adversarialUrl = "https://user:credential@example.test/private?prompt=secret#fragment";
  const requestEvidence = {
    method: "GET",
    resourceType: "Document",
    ...requestUrlEvidence(adversarialUrl),
  };
  const serializedRequestEvidence = JSON.stringify(requestEvidence);
  if (
    requestEvidence.method !== "GET"
    || requestEvidence.resourceType !== "Document"
    || requestEvidence.scheme !== "https"
    || requestEvidence.host !== "example.test"
    || /credential|private|prompt|secret|fragment/.test(serializedRequestEvidence)
  ) {
    throw new Error("request URL evidence must retain only structural URL fields");
  }
  const invalidUrlEvidence = requestUrlEvidence("not a URL with prompt=secret");
  if (
    invalidUrlEvidence.scheme !== null
    || invalidUrlEvidence.host !== null
    || JSON.stringify(invalidUrlEvidence).includes("prompt")
  ) {
    throw new Error("invalid request URLs must not be persisted");
  }

  const cases = [
    ["exact", firstSentinel, true],
    ["surrounding whitespace", `  ${firstSentinel}\n`, true],
    ["extra text", `${firstSentinel} done`, false],
    ["wrong sentinel", secondSentinel, false],
    ["repeated combined", `${firstSentinel}\n${firstSentinel}`, false],
    ["preamble", `Here is the result: ${firstSentinel}`, false],
    ["wrong case", "cold_phase_one_ok", false],
    ["trailing punctuation", `${firstSentinel}.`, false],
  ];
  for (const [name, text, expected] of cases) {
    const actual = sentinelTextVerdict(text, firstSentinel).matched;
    if (actual !== expected) throw new Error(`${name}: expected ${expected}, got ${actual}`);
  }

  function evaluateProbe(prompt, assistantText, expression, copyWrapperText = null) {
    const body = { innerText: `${prompt}\n${assistantText}` };
    const userMessage = {
      innerText: prompt,
      compareDocumentPosition: () => 4,
    };
    const assistantMessage = {
      innerText: assistantText,
      parentElement: body,
      contains: () => false,
    };
    const copyWrapper = copyWrapperText === null ? assistantMessage : {
      innerText: copyWrapperText,
      parentElement: assistantMessage,
      contains: () => false,
    };
    const copyButton = { parentElement: copyWrapper };
    const document = {
      body,
      querySelectorAll: (selector) => selector.includes("Edit user message")
        ? [userMessage]
        : selector.includes('button[aria-label="Copy"]') ? [copyButton] : [],
    };
    const Node = { DOCUMENT_POSITION_FOLLOWING: 4 };
    return Function("document", "Node", `return ${expression}`)(document, Node);
  }

  function evaluateAssistant(prompt, assistantText) {
    return evaluateProbe(
      prompt,
      assistantText,
      assistantOutputProbeExpression(prompt, firstSentinel)
    );
  }

  const promptWithSentinel = `Ignore ${firstSentinel}. ${firstPrompt}`;
  if (evaluateAssistant(promptWithSentinel, "not the sentinel").matched) {
    throw new Error("sentinel in the prompt satisfied the assistant oracle");
  }
  const anchored = evaluateAssistant(promptWithSentinel, firstSentinel);
  if (!anchored.matched) {
    throw new Error("prompt echo outside the assistant message affected the sentinel oracle");
  }

  const answer = firstSentinel;
  const timestamped = evaluateAssistant(firstPrompt, `${answer}\n6:23 PM`);
  if (!timestamped.matched || !timestamped.timestampRemoved || timestamped.text !== answer) {
    throw new Error("trailing renderer timestamp was not removed from the assistant answer");
  }
  if (textSha256(timestamped.text) !== textSha256(answer)) {
    throw new Error("renderer answer hash includes the removed trailing timestamp");
  }
  const rejectedTimestampCases = [
    ["middle timestamp", `${answer} 6:23 PM still present`],
    ["malformed timestamp", `${answer} 6:3 PM`],
    ["out-of-range hour", `${answer} 13:23 PM`],
    ["out-of-range minute", `${answer} 6:60 PM`],
    ["extra text before timestamp", `${answer} done\n6:23 PM`],
    ["repeated before timestamp", `${answer}\n${answer}\n6:23 PM`],
  ];
  for (const [name, text] of rejectedTimestampCases) {
    if (evaluateAssistant(firstPrompt, text).matched) {
      throw new Error(`${name}: renderer oracle should fail closed`);
    }
  }
  if (!defaultModeMenuVerdict("Plan mode\nTurn plan mode on")) {
    throw new Error("Default mode menu contract was not recognized");
  }
  if (defaultModeMenuVerdict("Plan mode\nTurn plan mode off")) {
    throw new Error("selected Plan mode was mistaken for Default mode");
  }
  if (!planModeIndicatorVerdict([{ ariaLabel: "Plan", text: "Plan" }])) {
    throw new Error("Plan mode indicator contract was not recognized");
  }
  if (planModeIndicatorVerdict([{ ariaLabel: null, text: "Plan mode" }])) {
    throw new Error("slash command was mistaken for selected Plan mode");
  }
  const nonemptyModeOutput = evaluateProbe(
    defaultModePrompt,
    "Default mode is active.\n6:23 PM",
    modeOutputProbeExpression(defaultModePrompt)
  );
  if (!nonemptyModeOutput.matched || nonemptyModeOutput.text !== "Default mode is active.") {
    throw new Error("nonempty mode output was not isolated from its renderer timestamp");
  }
  const timestampOnly = stripTrailingRendererTimestamp("6:23 PM");
  if (!timestampOnly.timestampRemoved || timestampOnly.text !== "") {
    throw new Error("timestamp-only renderer wrapper was mistaken for assistant output");
  }
  const nestedTimestampModeOutput = evaluateProbe(
    defaultModePrompt,
    "Default mode is active.\n6:23 PM",
    modeOutputProbeExpression(defaultModePrompt),
    "6:23 PM"
  );
  if (!nestedTimestampModeOutput.matched ||
      nestedTimestampModeOutput.text !== "Default mode is active.") {
    throw new Error("mode oracle stopped at a timestamp-only Copy-button ancestor");
  }
  const emptyModeOutput = evaluateProbe(
    defaultModePrompt,
    "",
    modeOutputProbeExpression(defaultModePrompt)
  );
  if (emptyModeOutput.matched) {
    throw new Error("empty mode output unexpectedly passed");
  }
  if (!projectSelectionVerdict({ count: 1, text: "workspace" }, "/tmp/run/workspace").matched) {
    throw new Error("exact renderer project name was not accepted");
  }
  if (projectSelectionVerdict({ count: 1, text: "other-project" }, "/tmp/run/workspace").matched) {
    throw new Error("wrong renderer project name was accepted");
  }
  if (projectSelectionVerdict({ count: 2, text: "workspace" }, "/tmp/run/workspace").matched) {
    throw new Error("duplicate renderer project controls were accepted");
  }
  if (!projectSelectionVerdict({ count: 1, text: "Selected: my workspace" },
    "/tmp/run/my workspace").matched) {
    throw new Error("space-containing renderer project name was not accepted");
  }
  if (!projectSelectionVerdict({ count: 1, text: "Selected 项目" }, "/tmp/run/项目").matched) {
    throw new Error("non-ASCII renderer project name was not accepted");
  }
  if (projectSelectionVerdict({ count: 1, text: "workspace-old" }, "/tmp/run/workspace").matched) {
    throw new Error("similarly prefixed renderer project name was accepted");
  }
  if (uniqueVisibleControlVerdict([]).reason !== "missing") {
    throw new Error("missing renderer control was not reported as pending");
  }
  if (uniqueVisibleControlVerdict([{ visible: true }, { visible: true }]).status !== "duplicate") {
    throw new Error("duplicate renderer controls were not rejected");
  }
  if (uniqueVisibleControlVerdict([{ disabled: true, visible: true }]).status !== "disabled") {
    throw new Error("disabled renderer control was not rejected");
  }
  if (uniqueVisibleControlVerdict([{ disabled: false, visible: false }]).reason !== "not-visible") {
    throw new Error("hidden renderer control was not reported as pending");
  }
  const readyControl = uniqueVisibleControlVerdict([{
    disabled: false, visible: true, x: 10, y: 20, width: 30, height: 40,
  }]);
  if (readyControl.status !== "ready" || readyControl.x !== 10 || readyControl.height !== 40) {
    throw new Error("unique visible renderer control coordinates were not retained");
  }
  if (worktreeCommandVerdict([{ text: "New worktree", disabled: false, visible: true }]).status !== "ready") {
    throw new Error("exact worktree slash-command option was not accepted");
  }
  if (worktreeCommandVerdict([{
    text: "New worktree Run this chat in a new worktree", disabled: false, visible: true,
  }]).status !== "ready") {
    throw new Error("described worktree slash-command option was not accepted");
  }
  if (worktreeCommandVerdict([{ text: "New remote worktree", disabled: false, visible: true }]).status !== "pending") {
    throw new Error("remote worktree command was mistaken for the exact local command");
  }
  if (worktreeCommandVerdict([
    { text: "New worktree", disabled: false, visible: true },
    { text: "New worktree", disabled: false, visible: true },
  ]).status !== "duplicate") {
    throw new Error("duplicate worktree slash-command options were not rejected");
  }
  if (worktreeModeVerdict([{ text: "Worktree", disabled: false, visible: true }]).status !== "ready") {
    throw new Error("selected worktree mode marker was not recognized");
  }
  if (worktreeModeVerdict([{ text: "Local", disabled: false, visible: true }]).status !== "pending") {
    throw new Error("local mode was mistaken for selected worktree mode");
  }
  const serializedCommandVerdict = Function(`return ${matchingControlExpression(
    '[{text: "New worktree", disabled: false, visible: true}]',
    ["New worktree", "New worktree Run this chat in a new worktree"]
  )}`)();
  if (serializedCommandVerdict.status !== "ready") {
    throw new Error("serialized worktree command verdict depends on outer scope");
  }
  const serializedModeVerdict = Function(`return ${matchingControlExpression(
    '[{text: "Worktree", disabled: false, visible: true}]',
    ["New worktree", "Worktree"]
  )}`)();
  if (serializedModeVerdict.status !== "ready") {
    throw new Error("serialized worktree mode verdict depends on outer scope");
  }

  let restorationPrepareCount = 0;
  const restorationEvents = [];
  const restoration = await ensureNativeProjectPickerFinalControl("/tmp/run/project", {
    probe: async () => ({ status: "pending", reason: "missing", count: 0 }),
    prepare: async () => { restorationPrepareCount += 1; },
    record: (kind, fields) => restorationEvents.push({ kind, fields }),
    pause: async () => {},
  });
  if (!restoration.restored || restorationPrepareCount !== 1 ||
      restorationEvents.length !== 1 ||
      restorationEvents[0].kind !== "native-project-picker-renderer-path-restored") {
    throw new Error("missing final picker control did not exercise exact renderer restoration");
  }
  let unexpectedPrepareCount = 0;
  const retained = await ensureNativeProjectPickerFinalControl("/tmp/run/project", {
    probe: async () => ({ status: "ready", count: 1 }),
    prepare: async () => { unexpectedPrepareCount += 1; },
    record: () => {},
    pause: async () => {},
  });
  if (retained.restored || unexpectedPrepareCount !== 0) {
    throw new Error("retained final picker control unexpectedly restored renderer path");
  }

  await expectRejected(
    () => targets({
      fetchImpl: (_url, { signal }) => new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
      }),
      deadlineMs: 5,
    }),
    "CDP target discovery timed out"
  );
  const stalledSocket = {
    addEventListener() {},
    removeEventListener() {},
  };
  await expectRejected(
    () => waitForSocketOpen(stalledSocket, 5),
    "CDP WebSocket open timed out"
  );
  const closingSocket = {
    addEventListener(kind, listener) {
      if (kind === "close") setTimeout(listener, 0);
    },
    removeEventListener() {},
  };
  await expectRejected(
    () => waitForSocketOpen(closingSocket, 50),
    "CDP WebSocket closed before open"
  );

  process.stdout.write("sentinel oracle self-test passed\n");
}

async function targets({ fetchImpl = fetch, deadlineMs = cdpCommandTimeoutMs } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), deadlineMs);
  try {
    const response = await fetchImpl(`${base}/json/list`, { signal: controller.signal });
    if (!response.ok) throw new Error(`CDP target list returned ${response.status}`);
    return await response.json();
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error(`CDP target discovery timed out after ${deadlineMs}ms`);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function waitForSocketOpen(socket, deadlineMs = cdpCommandTimeoutMs) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer;
    const finish = (action, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.removeEventListener("open", onOpen);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onClose);
      action(value);
    };
    const onOpen = () => finish(resolve, socket);
    const onError = () => finish(reject, new Error("CDP WebSocket error before open"));
    const onClose = () => finish(reject, new Error("CDP WebSocket closed before open"));
    timer = setTimeout(
      () => finish(reject, new Error(`CDP WebSocket open timed out after ${deadlineMs}ms`)),
      deadlineMs
    );
    socket.addEventListener("open", onOpen, { once: true });
    socket.addEventListener("error", onError, { once: true });
    socket.addEventListener("close", onClose, { once: true });
  });
}

if (process.argv[2] === "--self-test") {
  await runSelfTests();
  process.exit(0);
}

let target;
while (Date.now() - started < 20000) {
  try {
    const candidates = await targets();
    target = candidates.find((candidate) => candidate.type === "page");
    if (target) break;
  } catch {}
  await sleep(250);
}

if (!target) {
  emit("driver-error", { message: "No renderer CDP target appeared within 20 seconds" });
  process.exit(2);
}

emit("target", { id: target.id, title: target.title, url: target.url });
const ws = new WebSocket(target.webSocketDebuggerUrl);
let nextId = 1;
const pending = new Map();
let socketFailed = false;

function rejectPending(error) {
  for (const [id, entry] of pending) {
    clearTimeout(entry.timer);
    pending.delete(id);
    entry.reject(error);
  }
}

function failSocket(error) {
  if (socketFailed) return;
  socketFailed = true;
  rejectPending(error);
}

ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.method === "Network.requestWillBeSent") {
    const { request, type } = message.params;
    emit("request", {
      method: request.method,
      resourceType: type,
      ...requestUrlEvidence(request.url),
    });
  } else if (message.method === "Network.loadingFailed") {
    emit("request-failed", {
      blockedReason: message.params.blockedReason,
      errorText: message.params.errorText,
      type: message.params.type,
    });
  } else if (message.method === "Runtime.consoleAPICalled") {
    emit("console", {
      level: message.params.type,
      values: message.params.args.map((argument) =>
        argument.value ?? argument.description ?? argument.type
      ),
    });
  }
  if (message.id && pending.has(message.id)) {
    const { resolve, reject, timer } = pending.get(message.id);
    clearTimeout(timer);
    pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result);
  }
});
ws.addEventListener("error", () => failSocket(new Error("CDP WebSocket error")));
ws.addEventListener("close", () => failSocket(new Error("CDP WebSocket closed")));

await waitForSocketOpen(ws);

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    if (socketFailed) {
      reject(new Error("CDP WebSocket is unavailable"));
      return;
    }
    const id = nextId++;
    const timer = setTimeout(() => {
      if (!pending.has(id)) return;
      pending.delete(id);
      reject(new Error(`CDP command timed out: ${method}`));
    }, cdpCommandTimeoutMs);
    pending.set(id, { resolve, reject, timer });
    try {
      ws.send(JSON.stringify({ id, method, params }));
    } catch (error) {
      clearTimeout(timer);
      pending.delete(id);
      reject(error);
    }
  });
}

async function evaluate(expression) {
  const result = await send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text ?? "renderer evaluation failed");
  }
  return result.result?.value;
}

async function snapshot(label) {
  const value = await evaluate(`(() => ({
    label: ${JSON.stringify(label)},
    url: location.href,
    title: document.title,
    text: (document.body?.innerText ?? "").replace(/\\s+/g, " ").trim().slice(0, 12000),
    mainUi: Boolean(
      [...document.querySelectorAll("button")].some((element) => element.innerText?.includes("New task")) &&
      [...document.querySelectorAll("button")].some((element) => {
        const label = element.getAttribute("aria-label") ?? "";
        return label === "Choose project" || label.startsWith("Change project: ");
      }) &&
      [...document.querySelectorAll("button")].some((element) => element.getAttribute("aria-label") === "Open settings") &&
      document.querySelector('[contenteditable=true][data-codex-composer=true]')
    ),
    controls: [...document.querySelectorAll(
      "button, textarea, input, [contenteditable=true], [role=button], [role=link], [role=menuitem], [role=option], [role=tab], [role=switch]"
    )].slice(0, 220).map((element) => ({
      tag: element.tagName,
      role: element.getAttribute("role"),
      type: element.getAttribute("type"),
      ariaLabel: element.getAttribute("aria-label"),
      ariaChecked: element.getAttribute("aria-checked"),
      placeholder: element.getAttribute("placeholder"),
      text: (element.innerText || element.value || "").replace(/\\s+/g, " ").trim().slice(0, 260),
      disabled: Boolean(element.disabled),
    })),
  }))()`);
  emit("renderer-state", value);
  return value;
}

async function waitFor(predicateExpression, description, milliseconds = 30000) {
  const deadline = Date.now() + milliseconds;
  while (Date.now() < deadline) {
    if (await evaluate(`Boolean(${predicateExpression})`)) return true;
    await sleep(250);
  }
  emit("assertion-timeout", { description });
  return false;
}

async function clickMatching(expression, description) {
  const result = await evaluate(`(() => {
    const element = ${expression};
    if (!element || element.disabled) return false;
    element.click();
    return true;
  })()`);
  emit("action", { description, clicked: result });
  return result;
}

function visibleExactControlProbeExpression({ selector, exactText, accessibleName }) {
  return `(() => {
    const normalize = (value) => (value ?? "").replace(/\\s+/g, " ").trim();
    const candidates = [...document.querySelectorAll(${JSON.stringify(selector)})]
      .filter((element) => ${exactText == null
        ? `element.getAttribute("aria-label") === ${JSON.stringify(accessibleName)}`
        : `normalize(element.innerText ?? element.textContent) === ${JSON.stringify(exactText)}`})
      .map((element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {
          disabled: Boolean(element.disabled) || element.getAttribute("aria-disabled") === "true",
          visible: rect.width > 0 && rect.height > 0 &&
            rect.bottom > 0 && rect.right > 0 &&
            rect.top < window.innerHeight && rect.left < window.innerWidth &&
            style.display !== "none" && style.visibility === "visible" &&
            style.opacity !== "0" && style.pointerEvents !== "none",
          x: rect.left + rect.width / 2,
          y: rect.top + rect.height / 2,
          width: rect.width,
          height: rect.height,
          text: normalize(element.innerText ?? element.textContent),
          title: element.getAttribute("title"),
          ariaDescription: element.getAttribute("aria-description"),
        };
      });
    return (${uniqueVisibleControlVerdict.toString()})(candidates);
  })()`;
}

async function waitForUniqueVisibleControl(query, description, milliseconds = 10000) {
  const deadline = Date.now() + milliseconds;
  let lastResult = { status: "pending", reason: "not-probed", count: 0 };
  while (Date.now() < deadline) {
    lastResult = await evaluate(visibleExactControlProbeExpression(query));
    if (lastResult.status === "ready") return lastResult;
    if (lastResult.status === "duplicate" || lastResult.status === "disabled") {
      throw new Error(`${description} control is ${lastResult.reason}: ${JSON.stringify(lastResult)}`);
    }
    await sleep(100);
  }
  throw new Error(`${description} control did not become uniquely visible: ${JSON.stringify(lastResult)}`);
}

async function pressUniqueVisibleExactControl(query, description) {
  const readiness = await evaluate(visibleExactControlProbeExpression(query));
  if (readiness?.status !== "ready" || readiness.count !== 1 ||
      !Number.isFinite(readiness.x) || !Number.isFinite(readiness.y)) {
    throw new Error(`${description} control was not press-ready: ${JSON.stringify(readiness)}`);
  }
  await send("Input.dispatchMouseEvent", {
    type: "mousePressed", x: readiness.x, y: readiness.y,
    button: "left", buttons: 1, clickCount: 1,
  });
  await send("Input.dispatchMouseEvent", {
    type: "mouseReleased", x: readiness.x, y: readiness.y,
    button: "left", buttons: 0, clickCount: 1,
  });
}

function visibleCandidateExpression(selector) {
  return `[...document.querySelectorAll(${JSON.stringify(selector)})].map((element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return {
      element,
      text: (element.innerText ?? element.textContent ?? "").replace(/\\s+/g, " ").trim(),
      disabled: Boolean(element.disabled) || element.getAttribute("aria-disabled") === "true",
      visible: rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 &&
        rect.top < window.innerHeight && rect.left < window.innerWidth &&
        style.display !== "none" && style.visibility === "visible" &&
        style.opacity !== "0" && style.pointerEvents !== "none",
    };
  })`;
}

async function configureWorktreeRoot(expectedRoot) {
  if (!expectedRoot?.startsWith("/")) {
    throw new Error("worktree-first requires an absolute configured worktree root");
  }
  await waitForUniqueVisibleControl({
    selector: 'button[aria-label="Open settings"]', accessibleName: "Open settings",
  }, "Open settings");
  if (!await clickMatching(
    `[...document.querySelectorAll('button[aria-label="Open settings"]')].find((element) => !element.disabled)`,
    "open settings for worktree root"
  )) return false;
  await waitForUniqueVisibleControl({
    selector: 'button[aria-label="Worktrees"]', accessibleName: "Worktrees",
  }, "Worktrees settings");
  if (!await clickMatching(
    `[...document.querySelectorAll('button[aria-label="Worktrees"]')].find((element) => !element.disabled)`,
    "open Worktrees settings"
  )) return false;
  await waitForUniqueVisibleControl({
    selector: 'input[aria-label="Worktree root"]', accessibleName: "Worktree root",
  }, "Worktree root input");
  const updated = await evaluate(`(() => {
    const inputs = [...document.querySelectorAll('input[aria-label="Worktree root"]')]
      .filter((element) => !element.disabled);
    if (inputs.length !== 1) return { updated: false, count: inputs.length };
    const input = inputs[0];
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    if (!setter) return { updated: false, count: 1 };
    input.focus();
    setter.call(input, ${JSON.stringify(expectedRoot)});
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.blur();
    return { updated: input.value === ${JSON.stringify(expectedRoot)}, count: 1 };
  })()`);
  if (!updated?.updated || updated.count !== 1) {
    throw new Error(`worktree root input update failed closed: ${JSON.stringify(updated)}`);
  }
  const saved = await waitFor(
    `(document.body?.innerText ?? "").includes("Saved worktree root") &&
      document.querySelector('input[aria-label="Worktree root"]')?.value === ${JSON.stringify(expectedRoot)}`,
    "saved exact worktree root",
    10000
  );
  emit("worktree-root-saved", {
    saved,
    rootSha256: textSha256(expectedRoot),
  });
  if (!saved) return false;
  await waitForUniqueVisibleControl({
    selector: '[role="link"]', exactText: "Back to app",
  }, "Back to app");
  if (!await clickMatching(
    `[...document.querySelectorAll('[role="link"]')].find((element) =>
      (element.innerText ?? element.textContent ?? "").trim() === "Back to app"
    )`,
    "return from Worktrees settings"
  )) return false;
  return await reachMainUi();
}

async function selectNewWorktreeMode() {
  const focused = await evaluate(`(() => {
    const editor = document.querySelector('[contenteditable=true][data-codex-composer=true]');
    if (!editor || (editor.innerText ?? "").trim()) return false;
    editor.focus();
    return document.activeElement === editor;
  })()`);
  if (!focused) return false;
  await send("Input.insertText", { text: "/worktree" });
  const commandCandidates = visibleCandidateExpression(
    '[role="option"], [role="menuitem"], button'
  );
  const commandVerdict = matchingControlExpression(commandCandidates, [
    "New worktree",
    "New worktree Run this chat in a new worktree",
  ]);
  const commandReady = await waitFor(
    `${commandVerdict}.status === "ready"`,
    "unique exact New worktree slash command",
    10000
  );
  if (!commandReady) return false;
  const selected = await evaluate(`(() => {
    const candidates = ${commandCandidates};
    const verdict = (${matchingControlVerdict.toString()})(candidates, [
      "New worktree",
      "New worktree Run this chat in a new worktree",
    ]);
    if (verdict.status !== "ready") return { selected: false, status: verdict.status };
    const exact = candidates.filter((candidate) =>
      candidate.text === "New worktree" ||
      candidate.text === "New worktree Run this chat in a new worktree"
    );
    if (exact.length !== 1) return { selected: false, status: "duplicate" };
    exact[0].element.click();
    return { selected: true, status: "ready" };
  })()`);
  if (!selected?.selected) {
    throw new Error(`exact New worktree command was not selected: ${JSON.stringify(selected)}`);
  }
  const modeCandidates = visibleCandidateExpression("button");
  const modeVerdict = matchingControlExpression(
    modeCandidates, ["New worktree", "Worktree"]
  );
  const markerReady = await waitFor(
    `${modeVerdict}.status === "ready"`,
    "unique selected worktree mode marker",
    10000
  );
  const marker = markerReady
    ? await evaluate(modeVerdict)
    : { status: "pending", count: 0 };
  emit("worktree-mode-selected", {
    selected: markerReady && marker.status === "ready" && marker.count === 1,
    uniqueControl: marker.count === 1,
  });
  return markerReady && marker.status === "ready" && marker.count === 1;
}

// BEGIN_NATIVE_PROJECT_PICKER_REQUEST
async function prepareNativeProjectPicker(expectedFixtureRoot) {
  if (!expectedFixtureRoot?.startsWith("/")) {
    throw new Error("prepare-project-picker requires an absolute nonce fixture root");
  }
  const chooseProject = await waitForUniqueVisibleControl({
    selector: 'button[aria-label="Choose project"]',
    accessibleName: "Choose project",
  }, "Choose project");
  const preSelection = projectSelectionVerdict(chooseProject, expectedFixtureRoot);
  if (preSelection.matched) {
    throw new Error("nonce fixture was already selected before the native action");
  }
  emit("native-project-picker-precondition-ready", {
    uniqueControl: true,
    preconditionAccessibleName: "Choose project",
    preSelectionMatchedExpected: false,
    expectedFixtureSha256: textSha256(expectedFixtureRoot),
  });
  await pressUniqueVisibleExactControl({
    selector: 'button[aria-label="Choose project"]',
    accessibleName: "Choose project",
  }, "Choose project");
  emit("native-project-picker-control-clicked", {
    uniqueControl: true,
    accessibleName: "Choose project",
    expectedFixtureSha256: textSha256(expectedFixtureRoot),
  });
  await waitForUniqueVisibleControl({
    selector: '[role="menuitem"]', exactText: "New project",
  }, "New project");
  emit("renderer-project-menu-observed", {
    published: true,
    uniqueControl: true,
    exactText: "New project",
  });
  await pressUniqueVisibleExactControl({
    selector: '[role="menuitem"]', exactText: "New project",
  }, "New project");
  emit("renderer-new-project-menu-opened", {
    uniqueControl: true,
    exactText: "New project",
  });
  await waitForUniqueVisibleControl({
    selector: '[role="menuitem"]', exactText: "Use an existing folder",
  }, "Use an existing folder");
  emit("native-project-picker-final-control-ready", {
    uniqueControl: true,
    exactText: "Use an existing folder",
    expectedFixtureSha256: textSha256(expectedFixtureRoot),
  });
}

async function ensureNativeProjectPickerFinalControl(expectedFixtureRoot, {
  probe = async () => evaluate(visibleExactControlProbeExpression({
    selector: '[role="menuitem"]', exactText: "Use an existing folder",
  })),
  prepare = prepareNativeProjectPicker,
  record = emit,
  pause = sleep,
} = {}) {
  const finalControlQuery = {
    selector: '[role="menuitem"]', exactText: "Use an existing folder",
  };
  let finalReadiness = { status: "pending", reason: "not-probed", count: 0 };
  for (let attempt = 0; attempt < 5; attempt += 1) {
    finalReadiness = await probe(finalControlQuery);
    if (finalReadiness.status === "ready") break;
    if (finalReadiness.status === "duplicate" || finalReadiness.status === "disabled") {
      throw new Error(
        `Use an existing folder control is ${finalReadiness.reason}: ` +
        JSON.stringify(finalReadiness)
      );
    }
    await pause(100);
  }
  let restored = false;
  if (finalReadiness.status !== "ready") {
    await prepare(expectedFixtureRoot);
    record("native-project-picker-renderer-path-restored", {
      uniqueControl: true,
      exactPath: ["Choose project", "New project", "Use an existing folder"],
      expectedFixtureSha256: textSha256(expectedFixtureRoot),
    });
    restored = true;
  }
  return { finalControlQuery, restored };
}

async function requestNativeProjectPicker(expectedFixtureRoot) {
  if (!expectedFixtureRoot?.startsWith("/")) {
    throw new Error("open-project-picker requires an absolute nonce fixture root");
  }
  const { finalControlQuery } =
    await ensureNativeProjectPickerFinalControl(expectedFixtureRoot);
  await waitForUniqueVisibleControl({
    ...finalControlQuery,
  }, "Use an existing folder");
  await pressUniqueVisibleExactControl({
    ...finalControlQuery,
  }, "Use an existing folder");
  emit("native-project-picker-requested", {
    uniqueControl: true,
    exactText: "Use an existing folder",
    expectedFixtureSha256: textSha256(expectedFixtureRoot),
  });
  await snapshot("after-existing-folder-press");
}
// END_NATIVE_PROJECT_PICKER_REQUEST

async function confirmNativeProjectSelection(expectedFixtureRoot) {
  if (!expectedFixtureRoot?.startsWith("/")) {
    throw new Error("confirm-project-selection requires an absolute fixture root");
  }
  const expectedName = expectedFixtureRoot.split("/").filter(Boolean).at(-1) ?? "";
  if (!expectedName) throw new Error("fixture root has no project name");
  const control = await waitForUniqueVisibleControl({
    selector: 'button[aria-label^="Change project: "]',
    accessibleName: `Change project: ${expectedName}`,
  }, "selected project", 45000);
  const verdict = projectSelectionVerdict(control, expectedFixtureRoot);
  emit("renderer-project-selection-confirmed", {
    matched: verdict.matched,
    uniqueControl: control.count === 1,
    expectedFixtureSha256: textSha256(expectedFixtureRoot),
    visibleTextLength: verdict.visibleTextLength,
  });
  await snapshot("native-project-selection-confirmation");
  if (!verdict.matched) throw new Error("renderer did not expose the selected fixture name");
}

async function reachMainUi() {
  const deadline = Date.now() + 45000;
  let mainUi = false;
  while (Date.now() < deadline) {
    await clickMatching(
      `[...document.querySelectorAll("button")].find((element) => element.innerText?.trim() === "Skip")`,
      "skip local onboarding"
    );
    await clickMatching(
      `[...document.querySelectorAll("button")].find((element) => element.innerText?.trim() === "Continue with current model")`,
      "dismiss hosted model promotion"
    );
    mainUi = await evaluate(`
      [...document.querySelectorAll("button")].some((element) => element.innerText?.includes("New task")) &&
      [...document.querySelectorAll("button")].some((element) => {
        const label = element.getAttribute("aria-label") ?? "";
        return label === "Choose project" || label.startsWith("Change project: ");
      }) &&
      [...document.querySelectorAll("button")].some((element) => element.getAttribute("aria-label") === "Open settings") &&
      Boolean(document.querySelector('[contenteditable=true][data-codex-composer=true]')) &&
      !(document.body?.innerText ?? "").includes("What type of work do you do?")
    `);
    if (mainUi) break;
    await sleep(750);
  }
  await snapshot("main-ui");
  return Boolean(mainUi);
}

function assistantOutputProbeExpression(prompt, expectedSentinel) {
  return `(() => {
    const userMessage = [...document.querySelectorAll('[aria-label="Edit user message"]')]
      .filter((element) => (element.innerText ?? "").trim() === ${JSON.stringify(prompt)})
      .at(-1);
    if (!userMessage) return { matched: false, exactMatch: false };
    const copyButton = [...document.querySelectorAll('button[aria-label="Copy"]')]
      .filter((element) => userMessage.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
      .at(-1);
    if (!copyButton) return { matched: false, exactMatch: false };
    let assistantMessage = copyButton.parentElement;
    while (assistantMessage && assistantMessage !== document.body) {
      const { text } = (${stripTrailingRendererTimestamp.toString()})(
        assistantMessage.innerText ?? ""
      );
      const verdict = (${sentinelTextVerdict.toString()})(
        text, ${JSON.stringify(expectedSentinel)}
      );
      if (!assistantMessage.contains(userMessage) && verdict.matched) break;
      assistantMessage = assistantMessage.parentElement;
    }
    if (!assistantMessage || assistantMessage === document.body) {
      return { matched: false, exactMatch: false };
    }
    const rawText = assistantMessage.innerText ?? "";
    const stripped = (${stripTrailingRendererTimestamp.toString()})(rawText);
    const text = stripped.text.trim();
    const verdict = (${sentinelTextVerdict.toString()})(
      text, ${JSON.stringify(expectedSentinel)}
    );
    return {
      ...verdict,
      text,
      rawText,
      timestampRemoved: stripped.timestampRemoved,
      rendererTimestamp: stripped.timestamp,
    };
  })()`;
}

function modeOutputProbeExpression(prompt) {
  return `(() => {
    const userMessage = [...document.querySelectorAll('[aria-label="Edit user message"]')]
      .filter((element) => (element.innerText ?? "").trim() === ${JSON.stringify(prompt)})
      .at(-1);
    if (!userMessage) return { matched: false };
    const copyButton = [...document.querySelectorAll('button[aria-label="Copy"]')]
      .filter((element) => userMessage.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
      .at(-1);
    if (!copyButton) return { matched: false };
    let assistantMessage = copyButton.parentElement;
    while (assistantMessage && assistantMessage !== document.body) {
      const stripped = (${stripTrailingRendererTimestamp.toString()})(
        assistantMessage.innerText ?? ""
      );
      if (!assistantMessage.contains(userMessage) && stripped.text.trim()) break;
      assistantMessage = assistantMessage.parentElement;
    }
    if (!assistantMessage || assistantMessage === document.body) return { matched: false };
    const rawText = assistantMessage.innerText ?? "";
    const stripped = (${stripTrailingRendererTimestamp.toString()})(rawText);
    const text = stripped.text.trim();
    return {
      matched: text.length > 0,
      text,
      rawText,
      timestampRemoved: stripped.timestampRemoved,
      rendererTimestamp: stripped.timestamp,
    };
  })()`;
}

async function submitPrompt(prompt, expectedSentinel, expectedMode = null, outputPhase = phase) {
  const focused = await evaluate(`(() => {
    const editor = document.querySelector('[contenteditable=true][data-codex-composer=true]');
    if (!editor) return false;
    editor.focus();
    return document.activeElement === editor;
  })()`);
  if (!focused) return false;
  await send("Input.insertText", { text: prompt });
  const inserted = await waitFor(
    `(document.querySelector('[contenteditable=true][data-codex-composer=true]')?.innerText ?? "").includes(${JSON.stringify(prompt)})`,
    "trusted renderer composer insertion",
    5000
  );
  emit("action", { description: "populate renderer composer", inserted });
  if (!inserted) return false;

  const sendReady = await waitFor(
    `[...document.querySelectorAll("button")].some((element) =>
      /send/i.test(element.getAttribute("aria-label") ?? "") && !element.disabled
    )`,
    "enabled renderer send control",
    10000
  );
  let sent = false;
  if (sendReady) {
    sent = await clickMatching(
      `[...document.querySelectorAll("button")].find((element) =>
        /send/i.test(element.getAttribute("aria-label") ?? "") && !element.disabled
      )`,
      "submit renderer prompt"
    );
  } else {
    await snapshot("send-control-missing-using-enter");
    await send("Input.dispatchKeyEvent", {
      type: "keyDown",
      key: "Enter",
      code: "Enter",
      windowsVirtualKeyCode: 13,
      nativeVirtualKeyCode: 36,
      text: "\r",
    });
    await send("Input.dispatchKeyEvent", {
      type: "keyUp",
      key: "Enter",
      code: "Enter",
      windowsVirtualKeyCode: 13,
      nativeVirtualKeyCode: 36,
    });
    sent = await waitFor(
      `!Boolean(document.querySelector('[contenteditable=true][data-codex-composer=true]')) ||
        !(document.querySelector('[contenteditable=true][data-codex-composer=true]')?.innerText ?? "").includes(${JSON.stringify(prompt)})`,
      "renderer composer accepted Enter submission",
      5000
    );
    emit("action", { description: "submit renderer prompt with trusted Enter", sent });
  }
  if (!sent) return false;

  const assistantOutputProbe = expectedMode === null
    ? assistantOutputProbeExpression(prompt, expectedSentinel)
    : modeOutputProbeExpression(prompt);
  const completed = await waitFor(
    `${assistantOutputProbe}.matched`,
    expectedMode === null
      ? `exact renderer sentinel ${expectedSentinel} in assistant output`
      : `nonempty renderer output for ${expectedMode} mode turn`,
    timeoutMs
  );
  const outputOracle = await evaluate(assistantOutputProbe);
  const {
    text = "",
    rawText = "",
    rendererTimestamp = null,
    timestampRemoved = false,
    ...safeOutputOracle
  } = outputOracle;
  emit("assistant-output-oracle", {
    phase: outputPhase,
    ...safeOutputOracle,
    textLength: text.length,
    textSha256: textSha256(text),
    rawTextLength: rawText.length,
    rawTextSha256: textSha256(rawText),
    timestampRemoved,
    rendererTimestampSha256: rendererTimestamp
      ? textSha256(rendererTimestamp)
      : null,
  });
  if (expectedMode !== null) {
    emit("mode-turn-output", {
      mode: expectedMode,
      matched: Boolean(outputOracle.matched),
      promptSha256: textSha256(prompt),
      outputLength: text.length,
      outputSha256: textSha256(text),
    });
  }
  await snapshot(completed ? "renderer-reply-completed" : "renderer-reply-missing");
  return completed;
}

async function observeDefaultAndEnablePlanMode() {
  const editorFocused = await evaluate(`(() => {
    const editor = document.querySelector('[contenteditable=true][data-codex-composer=true]');
    if (!editor) return false;
    editor.focus();
    return document.activeElement === editor;
  })()`);
  if (!editorFocused) return { defaultObserved: false, planSelected: false };
  await send("Input.insertText", { text: "/plan" });
  const defaultObserved = await waitFor(
    `(${defaultModeMenuVerdict.toString()})(document.body?.innerText ?? "")`,
    "renderer-visible Default mode slash command",
    10000
  );
  await snapshot(defaultObserved ? "default-mode-control" : "default-mode-control-missing");
  const planSelected = defaultObserved && await clickMatching(
    `[...document.querySelectorAll('button, [role=option], [role=menuitem]')].find((element) => {
      const text = element.innerText ?? "";
      return text.includes("Plan mode") && text.includes("Turn plan mode on");
    })`,
    "select renderer Plan mode"
  );
  const indicatorObserved = planSelected && await waitFor(
    `(${planModeIndicatorVerdict.toString()})(
      [...document.querySelectorAll('button')].map((element) => ({
        ariaLabel: element.getAttribute('aria-label'), text: element.innerText ?? ""
      }))
    )`,
    "renderer-visible Plan mode indicator",
    10000
  );
  await snapshot(indicatorObserved ? "plan-mode-control" : "plan-mode-control-missing");
  return { defaultObserved, planSelected: indicatorObserved };
}

async function inspectTasks(prompt) {
  const taskPrefix = prompt.slice(0, 32);
  const taskVisible = await waitFor(
    `[...document.querySelectorAll('[role=button]')].some((element) =>
      (element.innerText ?? "").includes(${JSON.stringify(taskPrefix)})
    )`,
    "renderer-created local thread entry",
    15000
  );
  await snapshot("local-thread-entry");
  return { inspected: taskVisible };
}

async function reopenPersistedThread(state) {
  const taskPrefix = firstPrompt.slice(0, 32);
  const clicked = await clickMatching(
    `[...document.querySelectorAll('[role=button]')].find((element) =>
      (element.innerText ?? "").includes(${JSON.stringify(taskPrefix)})
    )`,
    `reopen persisted local thread ${state.threadId}`
  );
  if (!clicked) return { reopened: false, persistedOutputVisible: false };
  const firstPromptVisible = await waitFor(
    `[...document.querySelectorAll('[aria-label="Edit user message"]')].some((element) =>
      (element.innerText ?? "").trim() === ${JSON.stringify(firstPrompt)}
    )`,
    `persisted first prompt for ${state.threadId}`,
    15000
  );
  const firstOutputProbe = assistantOutputProbeExpression(firstPrompt, firstSentinel);
  const firstOutputSemanticMatch = firstPromptVisible && await waitFor(
    `${firstOutputProbe}.matched`,
    `persisted first output for ${state.threadId}`,
    15000
  );
  const reopenedOutput = firstOutputSemanticMatch
    ? await evaluate(firstOutputProbe)
    : { text: "" };
  const firstOutputVisible = firstOutputSemanticMatch &&
    textSha256(reopenedOutput.text) === state.firstRendererOutputSha256;
  emit("persisted-thread-oracle", {
    threadId: state.threadId,
    firstPromptVisible,
    firstOutputVisible,
  });
  await snapshot(firstOutputVisible ? "persisted-thread-reopened" : "persisted-thread-missing");
  return {
    reopened: firstPromptVisible && firstOutputVisible,
    persistedOutputVisible: firstOutputVisible,
  };
}

async function inspectSurfaces() {
  const main = await snapshot("surface-main");
  const modelControl = main.controls.find((control) =>
    control.text?.includes("Qwen3.5-2B-OptiQ-4bit (no-think)")
  );
  const rendererModelMetadataMatched = modelControl != null;
  const rendererFallbackModelMetadataAbsent =
    !main.controls.some((control) => /^Custom(?:\s|$)/.test(control.text ?? ""));
  const modelSurfaceObserved = main.controls.some((control) =>
    control === modelControl ||
    /model/i.test(control.ariaLabel ?? "")
  );

  const settingsOpened = await clickMatching(
    `[...document.querySelectorAll("button")].find((element) => element.getAttribute("aria-label") === "Open settings")`,
    "open settings"
  );
  if (settingsOpened) await sleep(750);
  const settings = await snapshot("settings");
  const settingsSurfaceObserved =
    settings.text.includes("Configuration") &&
    settings.text.includes("Plugins") &&
    settings.text.includes("Worktrees");

  const backToApp = await clickMatching(
    `[...document.querySelectorAll('[role=link]')].find((element) => element.textContent?.trim() === "Back to app")`,
    "return from settings"
  );
  if (backToApp) await sleep(750);
  const pluginsOpened = await clickMatching(
    `[...document.querySelectorAll("button")].find((element) => element.innerText?.trim() === "Plugins")`,
    "open plugin and skill library"
  );
  if (pluginsOpened) await sleep(750);
  const plugins = await snapshot("plugins");
  const pluginSurfaceObserved =
    plugins.text.includes("Plugins") &&
    plugins.text.includes("Skills") &&
    (plugins.text.includes("No plugins found") || plugins.text.includes("Search plugins"));
  const skillsOpened = await clickMatching(
    `[...document.querySelectorAll("button")].find((element) => element.innerText?.trim() === "Skills")`,
    "open skills"
  );
  if (skillsOpened) await sleep(750);
  const skills = await snapshot("skills");
  const skillSurfaceObserved =
    skills.text.includes("Skills") && skills.text.includes("Extend ChatGPT with task-specific skills");
  const localSkillVisible = skills.text.includes("local-sentinel");

  await clickMatching(
    `[...document.querySelectorAll("button")].find((element) => element.innerText?.includes("New task"))`,
    "return to new task composer"
  );
  await sleep(500);
  return {
    settingsSurfaceObserved,
    pluginSurfaceObserved,
    skillSurfaceObserved,
    localSkillVisible,
    modelSurfaceObserved,
    rendererModelMetadataMatched,
    rendererFallbackModelMetadataAbsent,
  };
}

await send("Network.enable");
await send("Runtime.enable");

try {
  if (phase === "prepare-project-picker") {
    const mainUi = await reachMainUi();
    if (!mainUi) throw new Error("main UI unavailable before native project picker preparation");
    await prepareNativeProjectPicker(phaseArgument);
  } else if (phase === "open-project-picker") {
    await requestNativeProjectPicker(phaseArgument);
  } else if (phase === "confirm-project-selection") {
    await confirmNativeProjectSelection(phaseArgument);
  } else if (phase === "first") {
    const mainUi = await reachMainUi();
    const rendererPromptCompleted = mainUi &&
      await submitPrompt(firstPrompt, firstSentinel);
    const tasks = rendererPromptCompleted
      ? await inspectTasks(firstPrompt)
      : { inspected: false };
    await clickMatching(
      `[...document.querySelectorAll("button")].find((element) => element.innerText?.includes("New task"))`,
      "return to new task before surface inspection"
    );
    await sleep(500);
    const surfaces = await inspectSurfaces();
    const summary = {
      mainUi,
      rendererPromptCompleted,
      tasksSurfaceObserved: tasks.inspected,
      ...surfaces,
      nativeProjectPickerExercised: false,
      nativePermissionDecisionExercised: false,
      nativeWorktreeControlExercised: false,
    };
    emit("gui-summary", summary);
    const required = [summary.mainUi, summary.rendererPromptCompleted, summary.tasksSurfaceObserved,
      summary.settingsSurfaceObserved, summary.pluginSurfaceObserved,
      summary.skillSurfaceObserved, summary.modelSurfaceObserved,
      summary.rendererModelMetadataMatched, summary.rendererFallbackModelMetadataAbsent];
    if (required.some((value) => value !== true)) process.exitCode = 1;
  } else if (phase === "worktree-first") {
    const mainUi = await reachMainUi();
    const worktreeRootSaved = mainUi && await configureWorktreeRoot(phaseArgument);
    const worktreeModeSelected = worktreeRootSaved && await selectNewWorktreeMode();
    const rendererPromptCompleted = worktreeModeSelected &&
      await submitPrompt(firstPrompt, firstSentinel, null, "first");
    const tasks = rendererPromptCompleted
      ? await inspectTasks(firstPrompt)
      : { inspected: false };
    await clickMatching(
      `[...document.querySelectorAll("button")].find((element) => element.innerText?.includes("New task"))`,
      "return to new task before worktree surface inspection"
    );
    await sleep(500);
    const surfaces = await inspectSurfaces();
    const summary = {
      mainUi,
      worktreeRootSaved,
      worktreeModeSelected,
      rendererPromptCompleted,
      tasksSurfaceObserved: tasks.inspected,
      ...surfaces,
      nativeProjectPickerExercised: false,
      nativePermissionDecisionExercised: false,
      nativeWorktreeControlExercised: worktreeModeSelected,
    };
    emit("worktree-first-summary", summary);
    emit("gui-summary", summary);
    const required = [summary.mainUi, summary.worktreeRootSaved,
      summary.worktreeModeSelected, summary.rendererPromptCompleted,
      summary.tasksSurfaceObserved, summary.settingsSurfaceObserved,
      summary.pluginSurfaceObserved, summary.skillSurfaceObserved,
      summary.modelSurfaceObserved, summary.rendererModelMetadataMatched,
      summary.rendererFallbackModelMetadataAbsent];
    if (required.some((value) => value !== true)) process.exitCode = 1;
  } else if (phase === "second" || phase === "worktree-second") {
    const resumeStatePath = phaseArgument;
    if (!resumeStatePath) throw new Error("second phase requires a resume-state path");
    const state = JSON.parse(readFileSync(resumeStatePath, "utf8"));
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(state.threadId)) {
      throw new Error(`invalid persisted thread identity: ${state.threadId}`);
    }
    if (state.firstPromptSha256 !== normalizedTextSha256(firstPrompt) ||
      state.firstSentinelSha256 !== textSha256(firstSentinel) ||
      !state.firstPersistedOutputSha256 ||
      !state.firstRendererOutputSha256 || !state.firstTurnIdSha256 ||
      state.firstOutputBinding !== "completed-turn" ||
      !state.cwdSha256 || state.cwdBinding !== "rollout-session-meta") {
      throw new Error("resume state does not bind the first deterministic turn");
    }
    const mainUi = await reachMainUi();
    const reopened = mainUi
      ? await reopenPersistedThread(state)
      : { reopened: false, persistedOutputVisible: false };
    const rendererContinuationCompleted = reopened.reopened &&
      await submitPrompt(secondPrompt, secondSentinel, null,
        phase === "worktree-second" ? "second" : phase);
    if (phase === "worktree-second") {
      emit("worktree-thread-reopened", {
        reopened: reopened.reopened,
        threadId: state.threadId,
        cwdSha256: state.cwdSha256,
      });
    }
    const summary = {
      mainUi,
      persistedThreadId: state.threadId,
      rendererThreadReopened: reopened.reopened,
      persistedOutputVisible: reopened.persistedOutputVisible,
      rendererContinuationCompleted,
    };
    emit(phase === "worktree-second" ? "worktree-second-summary" : "gui-resume-summary", summary);
    if (phase === "worktree-second") emit("gui-resume-summary", summary);
    const required = [summary.mainUi, summary.rendererThreadReopened,
      summary.persistedOutputVisible, summary.rendererContinuationCompleted];
    if (required.some((value) => value !== true)) process.exitCode = 1;
  } else if (phase === "modes") {
    const mainUi = await reachMainUi();
    let defaultModeControlObserved = false;
    let defaultPromptCompleted = false;
    let planModeControlObserved = false;
    let planPromptCompleted = false;
    if (mainUi) {
      const modeControls = await observeDefaultAndEnablePlanMode();
      defaultModeControlObserved = modeControls.defaultObserved;
      planModeControlObserved = modeControls.planSelected;
      if (planModeControlObserved) {
        await clickMatching(
          `[...document.querySelectorAll('button')].find((element) =>
            element.getAttribute('aria-label') === 'Plan' && element.innerText?.trim() === 'Plan'
          )`,
          "return renderer composer to Default mode"
        );
        defaultModeControlObserved = defaultModeControlObserved && await waitFor(
          `![...document.querySelectorAll('button')].some((element) =>
            element.getAttribute('aria-label') === 'Plan'
          )`,
          "renderer Plan indicator cleared for Default mode",
          5000
        );
        defaultPromptCompleted = defaultModeControlObserved &&
          await submitPrompt(defaultModePrompt, null, "default");
        const modeControlsAfterDefault = defaultPromptCompleted
          ? await observeDefaultAndEnablePlanMode()
          : { defaultObserved: false, planSelected: false };
        defaultModeControlObserved = defaultModeControlObserved && modeControlsAfterDefault.defaultObserved;
        planModeControlObserved = planModeControlObserved && modeControlsAfterDefault.planSelected;
        planPromptCompleted = planModeControlObserved &&
          await submitPrompt(planModePrompt, null, "plan");
      }
    }
    const summary = {
      mainUi,
      defaultModeControlObserved,
      defaultPromptCompleted,
      planModeControlObserved,
      planPromptCompleted,
    };
    emit("gui-modes-summary", summary);
    if (Object.values(summary).some((value) => value !== true)) process.exitCode = 1;
  } else {
    throw new Error(`unknown driver phase: ${phase}`);
  }
} catch (error) {
  emit("driver-error", { message: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
} finally {
  ws.close();
}
