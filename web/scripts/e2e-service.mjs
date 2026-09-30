import { spawn } from "node:child_process";
import { cpSync, existsSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
const web = resolve(root, "web");
// Do not inherit any live ContentFlow settings, credentials or API target.
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  !key.startsWith("CONTENTFLOW_") && !key.startsWith("NEXT_PUBLIC_CONTENTFLOW_")));
env.PYTHONUTF8 = "1";
env.PYTHONDONTWRITEBYTECODE = "1";
env.NEXT_TELEMETRY_DISABLED = "1";
env.CONTENTFLOW_E2E = "1";
env.NEXT_PUBLIC_CONTENTFLOW_API_BASE = "http://127.0.0.1:18765/api/v1";
const api = process.argv[2] === "api";
if (!api && process.argv[2] !== "web") throw new Error("Choose api or web");
const command = api
  ? resolve(root, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python")
  : process.execPath;
const args = api
  ? ["-B", "-X", "utf8", resolve(root, "tests/browser_server.py")]
  : [resolve(web, "node_modules/next/dist/bin/next"), "build"];
let child;
let stopping = false;
function launch(arguments_, build = false) {
  child = spawn(command, arguments_, { cwd: api ? root : web, env, stdio: "inherit", windowsHide: true });
  child.on("error", (error) => { console.error(error.message); process.exitCode = 1; });
  child.on("exit", (code) => {
    if (build && code === 0 && !stopping) {
      const standalone = resolve(web, ".next-e2e/standalone");
      // Next standalone intentionally excludes static/public files. Copy only
      // generated assets into this task's isolated build output.
      cpSync(resolve(web, ".next-e2e/static"), resolve(standalone, ".next-e2e/static"), { recursive: true });
      if (existsSync(resolve(web, "public"))) cpSync(resolve(web, "public"), resolve(standalone, "public"), { recursive: true });
      env.PORT = "18766";
      env.HOSTNAME = "127.0.0.1";
      launch([resolve(standalone, "server.js")]);
    } else process.exitCode = code ?? 1;
  });
}
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => {
  stopping = true;
  child?.kill(signal);
});
launch(args, !api);
