#!/usr/bin/env node
// THROWAWAY PROTOTYPE ONLY: drive renderer-only ticket 12 checks over loopback CDP.

import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

const port = Number(process.argv[2]);
const phase = process.argv[3] ?? "first";
const timeoutMs = Number(process.argv[4] ?? 120000);
const resumeStatePath = process.argv[5];
const base = `http://127.0.0.1:${port}`;
const started = Date.now();
const firstPrompt = "Reply exactly COLD_PHASE_ONE_OK and nothing else. Do not use tools.";
const firstSentinel = "COLD_PHASE_ONE_OK";
const secondPrompt = "Reply exactly COLD_PHASE_TWO_OK and nothing else. Do not use tools.";
const secondSentinel = "COLD_PHASE_TWO_OK";
const defaultModePrompt = "Confirm Default mode in one short sentence. Do not use tools.";
const planModePrompt = "Confirm Plan mode in one short sentence. Do not use tools.";

function emit(kind, data) {
  process.stdout.write(`${JSON.stringify({ at: new Date().toISOString(), kind, phase, ...data })}\n`);
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
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

function runSelfTests() {
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
  process.stdout.write("sentinel oracle self-test passed\n");
}

async function targets() {
  const response = await fetch(`${base}/json/list`);
  if (!response.ok) throw new Error(`CDP target list returned ${response.status}`);
  return response.json();
}

if (process.argv[2] === "--self-test") {
  runSelfTests();
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
      values: message.params.args.map((argument) =>
        argument.value ?? argument.description ?? argument.type
      ),
    });
  }
  if (message.id && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result);
  }
});

await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve, { once: true });
  ws.addEventListener("error", reject, { once: true });
});

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
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
      [...document.querySelectorAll("button")].some((element) => element.getAttribute("aria-label") === "Choose project") &&
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
      [...document.querySelectorAll("button")].some((element) => element.getAttribute("aria-label") === "Choose project") &&
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

async function submitPrompt(prompt, expectedSentinel, expectedMode = null) {
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
  const rendererModelMetadataMatched =
    main.text.includes("Local OptiQ smoke model") ||
    main.text.includes("Qwen3.5-2B-OptiQ-4bit");
  const modelSurfaceObserved = main.controls.some((control) =>
    control.text === "Custom Light" ||
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
  };
}

await send("Network.enable");
await send("Runtime.enable");

try {
  if (phase === "first") {
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
      summary.skillSurfaceObserved, summary.modelSurfaceObserved];
    if (required.some((value) => value !== true)) process.exitCode = 1;
  } else if (phase === "second") {
    if (!resumeStatePath) throw new Error("second phase requires a resume-state path");
    const state = JSON.parse(readFileSync(resumeStatePath, "utf8"));
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(state.threadId)) {
      throw new Error(`invalid persisted thread identity: ${state.threadId}`);
    }
    if (state.firstPromptSha256 !== normalizedTextSha256(firstPrompt) ||
      state.firstSentinelSha256 !== textSha256(firstSentinel) ||
      !state.firstPersistedOutputSha256 ||
      !state.firstRendererOutputSha256 || !state.firstTurnIdSha256 ||
      state.firstOutputBinding !== "completed-turn") {
      throw new Error("resume state does not bind the first deterministic turn");
    }
    const mainUi = await reachMainUi();
    const reopened = mainUi
      ? await reopenPersistedThread(state)
      : { reopened: false, persistedOutputVisible: false };
    const rendererContinuationCompleted = reopened.reopened &&
      await submitPrompt(secondPrompt, secondSentinel);
    const summary = {
      mainUi,
      persistedThreadId: state.threadId,
      rendererThreadReopened: reopened.reopened,
      persistedOutputVisible: reopened.persistedOutputVisible,
      rendererContinuationCompleted,
    };
    emit("gui-resume-summary", summary);
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
