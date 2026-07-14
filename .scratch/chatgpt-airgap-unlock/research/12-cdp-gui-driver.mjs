#!/usr/bin/env node
// THROWAWAY PROTOTYPE ONLY: drive renderer-only ticket 12 checks over loopback CDP.

const port = Number(process.argv[2]);
const phase = process.argv[3] ?? "first";
const timeoutMs = Number(process.argv[4] ?? 120000);
const base = `http://127.0.0.1:${port}`;
const started = Date.now();
const firstPrompt = "Reply exactly LOCAL_RENDERER_OK and nothing else.";
const firstReply = "LOCAL_RENDERER_OK";

function emit(kind, data) {
  process.stdout.write(`${JSON.stringify({ at: new Date().toISOString(), kind, phase, ...data })}\n`);
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
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

async function submitPrompt(prompt, expectedReply) {
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

  const completed = await waitFor(
    `(document.body?.innerText ?? "").split("\\n").some((line) => line.trim() === ${JSON.stringify(expectedReply)})`,
    `exact renderer reply ${expectedReply}`,
    timeoutMs
  );
  await snapshot(completed ? "renderer-reply-completed" : "renderer-reply-missing");
  return completed;
}

async function inspectTasks() {
  const clicked = await clickMatching(
    `[...document.querySelectorAll("button")].find((element) => element.innerText?.trim() === "Tasks")`,
    "open Tasks"
  );
  if (!clicked) return { inspected: false, reopened: false };
  await sleep(1000);
  const taskVisible = await waitFor(
    `!Boolean(document.querySelector('[contenteditable=true][data-codex-composer=true]')) &&
      !(document.body?.innerText ?? "").includes("No tasks") &&
      [...document.querySelectorAll("button, [role=button], a")].some((element) => {
        const text = (element.innerText ?? "").trim();
        return text.length > 0 && !["Tasks", "New task", "Search", "Plugins", "Project", "Settings"].includes(text);
      })`,
    "renderer-created task in Tasks",
    15000
  );
  await snapshot("tasks");
  return { inspected: taskVisible };
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
  if (phase !== "first") throw new Error(`unknown driver phase: ${phase}`);
  const mainUi = await reachMainUi();
  const rendererPromptCompleted = mainUi && await submitPrompt(firstPrompt, firstReply);
  const tasks = rendererPromptCompleted
    ? await inspectTasks()
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
} catch (error) {
  emit("driver-error", { message: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
} finally {
  ws.close();
}
