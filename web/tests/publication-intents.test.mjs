import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

function runtime(getItem, overrides = {}) {
  const data = new Map();
  const storage = { getItem: getItem ?? ((key) => data.get(key) ?? null),
    setItem: (key, value) => data.set(key, value), removeItem: (key) => data.delete(key), ...overrides };
  const context = vm.createContext({ window: {}, sessionStorage: storage });
  const code = ts.transpileModule(readFileSync("lib/publication-intents.ts", "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInContext(`(function(exports,require){${code}\n})`, context)(exports, () => ({}));
  return { ...exports, data, storage };
}

const intent = { request_id: "TEST-ONLY-operation", preview_token: "x".repeat(80),
  content_item_id: "content-a", channel_id: "channel-a", delivery_mode: "connector", publish_now: true };

test("corrupt publication receipt cannot masquerade as no pending operation", () => {
  assert.throws(() => runtime(() => "{corrupt").readPendingPublication("key"));
});
test("unreadable publication receipt cannot masquerade as no pending operation", () => {
  assert.throws(() => runtime(() => { throw new Error("TEST-ONLY denied"); }).readPendingPublication("key"));
});
test("incomplete publication intent cannot be replayed", () => {
  const raw = JSON.stringify({ request_id: "test-intent-0001", preview_token: "x".repeat(80), content_item_id: "a", channel_id: "b" });
  assert.throws(() => runtime(() => raw).readPendingPublication("key"));
});

test("valid immediate and scheduled records survive restoration without mutation", () => {
  for (const value of [intent, { ...intent, publish_now: false, scheduled_at: "2030-11-03T01:30:00-04:00" }]) {
    const raw = JSON.stringify(value);
    assert.equal(JSON.stringify(runtime(() => raw).readPendingPublication("key")), raw);
  }
});

for (const change of [{ publish_now: "false" }, { delivery_mode: "auto" }, { request_id: "bad" },
  { scheduled_at: "2030-01-01T00:00:00Z" }, { publish_now: false },
  { publish_now: false, scheduled_at: "2030-01-01T00:00:00" }, { extra: "field" }]) {
  test(`invalid intent is blocked: ${JSON.stringify(change)}`, () => {
    assert.throws(() => runtime(() => JSON.stringify({ ...intent, ...change })).readPendingPublication("key"));
  });
}

test("persist permits only exact original replay and verifies writing", () => {
  const r = runtime();
  r.persistPublicationIntent("key", intent);
  r.persistPublicationIntent("key", { ...intent });
  assert.throws(() => r.persistPublicationIntent("key", { ...intent, request_id: "TEST-ONLY-another" }));
  assert.equal(r.data.get("key"), JSON.stringify(intent));
  assert.throws(() => runtime(undefined, { setItem: () => {} }).persistPublicationIntent("key", intent));
  assert.throws(() => runtime(undefined, { setItem: () => { throw new Error("denied"); } }).persistPublicationIntent("key", intent));
});

test("late cleanup cannot remove another request, but duplicate cleanup is idempotent", () => {
  const r = runtime();
  r.persistPublicationIntent("key", intent);
  assert.throws(() => r.clearPublicationIntent("key", { ...intent, channel_id: "different" }));
  assert.equal(r.data.get("key"), JSON.stringify(intent));
  r.clearPublicationIntent("key", intent);
  r.clearPublicationIntent("key", intent);
  assert.equal(r.data.size, 0);
});

test("silent removal failure retains the original receipt", () => {
  const r = runtime(undefined, { removeItem: () => {} });
  r.persistPublicationIntent("key", intent);
  assert.throws(() => r.clearPublicationIntent("key", intent));
  assert.equal(r.data.get("key"), JSON.stringify(intent));
});

test("manual corrupt cleanup refuses changed or newly valid storage", () => {
  const r = runtime();
  r.data.set("key", "{corrupt");
  assert.ok(r.inspectPublicationIntent("key").error);
  assert.throws(() => r.discardInvalidPublication("key", "old snapshot"));
  r.data.set("key", JSON.stringify(intent));
  assert.throws(() => r.discardInvalidPublication("key", JSON.stringify(intent)));
  r.data.set("key", "{corrupt");
  r.discardInvalidPublication("key", "{corrupt");
  assert.equal(r.data.size, 0);
});

test("null string is corrupt, absent storage is empty, and unavailable is distinguishable", () => {
  assert.throws(() => runtime(() => "null").readPendingPublication("key"));
  assert.equal(runtime().readPendingPublication("key"), null);
  const r = runtime(() => { throw new Error("denied"); });
  assert.equal(r.inspectPublicationIntent("key").unavailable, true);
});
