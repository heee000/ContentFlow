import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

// Execute the actual TypeScript modules with isolated browser dependencies.
// No copied protocol logic, server, storage files or network calls.
function runtime(fetch, { deniedStorage = false, configurableApi = true } = {}) {
  const values = new Map();
  const storage = {
    getItem(key) { if (deniedStorage) throw new Error("Storage denied"); return values.get(key) ?? null; },
    setItem(key, value) { if (deniedStorage) throw new Error("Storage denied"); values.set(key, value); },
    removeItem(key) { if (deniedStorage) throw new Error("Storage denied"); values.delete(key); },
  };
  const scope = vm.createContext({ fetch, window: new EventTarget(), localStorage: storage,
    navigator: {}, Headers, Response, FormData, URL, URLSearchParams, Event, AbortController, AbortSignal, setTimeout, clearTimeout });
  const modules = new Map();
  function load(name) {
    if (name === "@/security") return { DEFAULT_API_BASE: "https://test-only.invalid/api/v1", RUNTIME_API_BASE_CONFIGURABLE: configurableApi };
    if (modules.has(name)) return modules.get(name).exports;
    assert.match(name, /^\.\/[a-z-]+$/);
    const source = readFileSync(new URL(`../lib/${name.slice(2)}.ts`, import.meta.url), "utf8");
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    const compiledModule = { exports: {} };
    modules.set(name, compiledModule);
    vm.runInContext(`(function(exports, require, module) {${code}\n})`, scope)(compiledModule.exports, load, compiledModule);
    return compiledModule.exports;
  }
  return { api: load("./contentflow-api"), session: load("./browser-session"), errors: load("./api-errors"), storage };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

const identity = (context) => ({ context: context.repeat(64), user: { id: "TEST", email: "test@example.com", display_name: "Test" }, workspace: { id: context, name: context }, role: "admin" });

test("awaits rejection of a late response, with no subsequent page request", async () => {
  const response = deferred();
  let requests = 0;
  const app = runtime(() => { requests += 1; return response.promise; });
  app.session.activateBrowserSession(identity("a"));
  const pending = app.api.apiAllPages("/campaigns");
  app.session.activateBrowserSession(identity("b"));
  const rejected = assert.rejects(pending, (error) => error instanceof app.errors.StaleResponseError);
  response.resolve(new Response('[{"id":"old"}]', { headers: { "X-ContentFlow-Next-Cursor": "next" } }));
  await rejected;
  assert.equal(requests, 1);
});

test("context change while JSON body is pending cannot return old data", async () => {
  const body = deferred();
  const entered = deferred();
  const response = new Response();
  response.json = () => { entered.resolve(); return body.promise; };
  const app = runtime(async () => response);
  app.session.activateBrowserSession(identity("a"));
  const pending = app.api.api("/campaigns");
  await entered.promise;
  app.session.activateBrowserSession(identity("b"));
  const rejected = assert.rejects(pending, (error) => error instanceof app.errors.StaleResponseError);
  body.resolve([{ id: "old" }]);
  await rejected;
});

test("pagination cannot adopt a new session between pages", async () => {
  const second = deferred();
  const entered = deferred();
  const contexts = [];
  const app = runtime(async (_url, init) => {
    contexts.push(init.headers.get("X-ContentFlow-Context"));
    if (contexts.length === 1) return new Response('[{"id":"one"}]', { headers: { "X-ContentFlow-Next-Cursor": "two" } });
    entered.resolve();
    return second.promise;
  });
  app.session.activateBrowserSession(identity("a"));
  const pending = app.api.apiAllPages("/campaigns");
  await entered.promise;
  app.session.activateBrowserSession(identity("b"));
  const rejected = assert.rejects(pending, (error) => error instanceof app.errors.StaleResponseError);
  second.resolve(new Response('[{"id":"two"}]', { headers: { "X-ContentFlow-Next-Cursor": "three" } }));
  await rejected;
  assert.deepEqual(contexts, ["a".repeat(64), "a".repeat(64)]);
});

test("asset body resolved after context change never reaches the caller", async () => {
  const body = deferred();
  const entered = deferred();
  const response = new Response();
  response.blob = () => { entered.resolve(); return body.promise; };
  const app = runtime(async () => response);
  app.session.activateBrowserSession(identity("a"));
  const pending = app.api.apiBlob("/assets/TEST/download");
  await entered.promise;
  app.session.activateBrowserSession(identity("b"));
  const rejected = assert.rejects(pending, (error) => error instanceof app.errors.StaleResponseError);
  body.resolve(new Blob(["TEST-ONLY"]));
  await rejected;
});

test("another page's API selection cannot retarget an active page", async () => {
  const targets = [];
  const app = runtime(async (url) => { targets.push(url); return new Response("[]"); });
  app.session.activateBrowserSession(identity("a"));
  app.storage.setItem("contentflow_api_base", "https://other-test-only.invalid/api/v1");
  await app.api.api("/campaigns");
  assert.deepEqual(targets, ["https://test-only.invalid/api/v1/campaigns"]);
});

test("denied local storage still permits an explicit in-memory session", async () => {
  const app = runtime(async () => new Response("[]"), { deniedStorage: true });
  app.session.activateBrowserSession(identity("a"));
  assert.deepEqual(await app.api.api("/campaigns"), []);
});

test("production API selection ignores persisted and explicit overrides", async () => {
  const targets = [];
  const app = runtime(async (url) => { targets.push(url); return new Response("[]"); }, { configurableApi: false });
  app.storage.setItem("contentflow_api_base", "https://other-test-only.invalid/api/v1");
  app.session.activateBrowserSession(identity("a"));
  assert.equal(app.api.runtimeApiBaseConfigurable, false);
  assert.equal(app.storage.getItem("contentflow_api_base"), null);
  app.api.setApiBase("https://other-test-only.invalid/api/v1");
  await app.api.api("/campaigns");
  assert.deepEqual(targets, ["https://test-only.invalid/api/v1/campaigns"]);
});

test("discarding a stale response cancels its unread body", async () => {
  const response = deferred();
  let cancelled = 0;
  const body = new ReadableStream({ cancel() { cancelled += 1; } });
  const app = runtime(() => response.promise);
  app.session.activateBrowserSession(identity("a"));
  const pending = app.api.apiAllPages("/campaigns");
  app.session.activateBrowserSession(identity("b"));
  const rejected = assert.rejects(pending, (error) => error instanceof app.errors.StaleResponseError);
  response.resolve(new Response(body, { headers: { "Cache-Control": "no-store", "X-ContentFlow-Next-Cursor": "next" } }));
  await rejected;
  assert.equal(cancelled, 1);
});

test("an expired restore probe releases its body before refusing unsupported refresh", async () => {
  let cancelled = 0;
  const body = new ReadableStream({ cancel() { cancelled += 1; } });
  const app = runtime(async () => new Response(body, { status: 401, headers: { "Cache-Control": "no-store" } }));
  await assert.rejects(app.session.restoreBrowserSession(), (error) => error.code === "session_reauthentication_required");
  assert.equal(cancelled, 1);
});

test("an expired business response is released without retrying when Web Locks is unavailable", async () => {
  let cancelled = 0;
  let requests = 0;
  const body = new ReadableStream({ cancel() { cancelled += 1; } });
  const app = runtime(async () => {
    requests += 1;
    return new Response(body, { status: 401 });
  });
  app.session.activateBrowserSession(identity("a"));
  await assert.rejects(app.api.api("/campaigns"), (error) => error.code === "session_reauthentication_required");
  assert.equal(cancelled, 1);
  assert.equal(requests, 1);
  assert.throws(() => app.session.captureContext(), (error) => error.code === "session_context_changed");
});

for (const cleanup of ["throws", "rejects"]) {
  test(`body cleanup that ${cleanup} cannot replace a stale-context rejection`, async () => {
    const response = deferred();
    let cancelled = 0;
    const app = runtime(() => response.promise);
    app.session.activateBrowserSession(identity("a"));
    const pending = app.api.api("/campaigns");
    app.session.activateBrowserSession(identity("b"));
    const rejected = assert.rejects(pending, (error) => error instanceof app.errors.StaleResponseError);
    response.resolve({ body: { cancel() {
      cancelled += 1;
      const error = new Error("TEST-ONLY cleanup failure");
      if (cleanup === "throws") throw error;
      return Promise.reject(error);
    } } });
    await rejected;
    assert.equal(cancelled, 1);
  });
}
