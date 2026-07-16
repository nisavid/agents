#!/usr/bin/env node
// Focused regressions for bounded CDP discovery and WebSocket opening.

import assert from "node:assert/strict";
import { fetchTargets, openWebSocket, redactCdpMessage } from "./08-cdp-observer.mjs";

let lastSocket;
class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.closed = false;
    this.listeners = new Map();
    lastSocket = this;
  }

  addEventListener(kind, callback) {
    this.listeners.set(kind, callback);
  }

  emit(kind, value = {}) {
    this.listeners.get(kind)?.(value);
  }

  close() {
    this.closed = true;
  }
}

async function main() {
  let signal;
  const started = performance.now();
  await assert.rejects(
    fetchTargets(
      "http://127.0.0.1:49308",
      25,
      (_url, options) => {
        signal = options.signal;
        return new Promise((_resolve, reject) => {
          options.signal.addEventListener("abort", () => reject(options.signal.reason));
        });
      },
    ),
    /aborted|AbortError/i,
  );
  assert.equal(signal.aborted, true);
  assert.ok(performance.now() - started < 500);

  const responseStarted = performance.now();
  assert.deepEqual(
    await fetchTargets("http://127.0.0.1:49308", 100, async () => ({
      ok: true,
      json: async () => [{ type: "page" }],
    })),
    [{ type: "page" }],
  );
  assert.ok(performance.now() - responseStarted < 500);

  const opened = openWebSocket("ws://127.0.0.1:49308/devtools", 100, FakeWebSocket);
  setTimeout(() => lastSocket.emit("open"), 0);
  assert.equal(await opened, lastSocket);

  await assert.rejects(
    openWebSocket("ws://127.0.0.1:49308/hung", 25, FakeWebSocket),
    /did not open/,
  );
  assert.equal(lastSocket.closed, true);

  const secret = "sk-live-redaction-regression";
  const request = redactCdpMessage({
    method: "Network.requestWillBeSent",
    params: { request: { method: "GET", url: `https://example.test/path?token=${secret}` }, type: "Fetch" },
  });
  const consoleEvent = redactCdpMessage({
    method: "Runtime.consoleAPICalled",
    params: { type: "log", args: [{ value: `prompt ${secret}` }] },
  });
  const state = redactCdpMessage({
    id: 1,
    result: {
      result: {
        value: {
          url: `file:///private/tmp/${secret}`,
          title: secret,
          text: `document ${secret}`,
          readyState: "complete",
          mainUi: true,
          controls: [{ text: secret, disabled: false }],
          likelyBridgeGlobals: [secret],
          electronBridgeShape: { [secret]: "function" },
        },
      },
    },
  });
  const evidence = JSON.stringify([request, consoleEvent, state]);
  assert.doesNotMatch(evidence, new RegExp(secret));
  assert.equal(request.data.url.length, `https://example.test/path?token=${secret}`.length);
  assert.match(request.data.url.sha256, /^[a-f0-9]{64}$/);
  assert.equal(state.data.mainUi, true);
  assert.equal(state.data.controls.count, 1);

  console.log("CDP discovery, WebSocket deadline, and evidence-redaction regressions passed");
}

await main();
