import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

function runtime(overrides = {}) {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key), ...overrides };
  const scope = vm.createContext({ sessionStorage: storage });
  const source = readFileSync(new URL("../lib/generation-intents.ts", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const exports = {};
  vm.runInContext(`(function(exports) {${code}\n})`, scope)(exports);
  return { api: exports, values };
}

const intent = { version: 1, requestId: "TEST-ONLY-intent-0001", campaignId: "campaign-a", campaignName: "TEST-ONLY",
  body: { expected_campaign_updated_at: "2026-09-24T00:00:00Z" } };

test("generation receipt scope distinguishes API, user and workspace", () => {
  const { api } = runtime();
  const keys = [["api", "a", "w"], ["api2", "a", "w"], ["api", "b", "w"], ["api", "a", "w2"]]
    .map((parts) => api.generationScope(...parts));
  assert.equal(new Set(keys).size, 4);
});

test("saved generation intent restores unchanged after a new module instance", () => {
  const first = runtime();
  first.api.persistGenerationIntent("key", intent);
  const restored = runtime({ getItem: (key) => first.values.get(key) ?? null });
  assert.equal(JSON.stringify(restored.api.readGenerationIntent("key")), JSON.stringify(intent));
});

test("cannot replace a pending generation with a new operation", () => {
  const { api, values } = runtime();
  api.persistGenerationIntent("key", intent);
  assert.throws(() => api.persistGenerationIntent("key", { ...intent, requestId: "TEST-ONLY-new-0002" }));
  assert.equal(values.get("key"), JSON.stringify(intent));
});

test("unavailable and silently dropped storage fail before sending is possible", () => {
  const denied = runtime({ setItem: () => { throw new Error("TEST-ONLY denied"); } });
  assert.throws(() => denied.api.persistGenerationIntent("key", intent));
  const dropped = runtime({ setItem: () => {} });
  assert.throws(() => dropped.api.persistGenerationIntent("key", intent));
});

for (const raw of ["bad-json", JSON.stringify({ ...intent, version: 2 }), JSON.stringify({ ...intent, body: {} }),
  JSON.stringify({ ...intent, body: { ...intent.body, provider: "unexpected" } })]) {
  test(`invalid persisted intent is not silently treated as empty: ${raw.slice(0, 28)}`, () => {
    const { api } = runtime({ getItem: () => raw });
    assert.throws(() => api.readGenerationIntent("key"));
  });
}

test("clearing a receipt cannot delete another pending operation", () => {
  const { api, values } = runtime();
  api.persistGenerationIntent("key", intent);
  assert.throws(() => api.clearGenerationIntent("key", { ...intent, requestId: "TEST-ONLY-other-0003" }));
  assert.equal(values.get("key"), JSON.stringify(intent));
  api.clearGenerationIntent("key", intent);
  assert.equal(api.readGenerationIntent("key"), null);
  api.clearGenerationIntent("key", intent); // another consumer of the same receipt
});

test("failed receipt cleanup remains recoverable with its original operation", () => {
  const { api, values } = runtime({ removeItem: () => {} });
  api.persistGenerationIntent("key", intent);
  assert.throws(() => api.clearGenerationIntent("key", intent));
  assert.equal(values.get("key"), JSON.stringify(intent));
});
