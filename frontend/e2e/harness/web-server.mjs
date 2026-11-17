// Builds the web app into .next-e2e (never the .next directory a dev server uses) with /api
// proxied to the test API, then serves the standalone output the same way the Docker image
// does (`node server.js` with the static files copied next to it) on 127.0.0.1:E2E_WEB_PORT.
//
// E2E_SKIP_BUILD=1 reuses the previous e2e build when it was made for the same API address.
// It is faster while writing tests but runs stale code if the app changed since that build.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, "..", "..");
const distDir = ".next-e2e";
const dist = path.join(frontend, distDir);
const standalone = path.join(dist, "standalone");
const port = Number(process.env.E2E_WEB_PORT ?? 3765);
const apiPort = Number(process.env.E2E_API_PORT ?? 8765);
const backendUrl = `http://127.0.0.1:${apiPort}`;
const next = path.join(frontend, "node_modules", ".bin", process.platform === "win32" ? "next.cmd" : "next");
const stamp = path.join(dist, "e2e-backend-url");

const env = {
  ...process.env,
  NEWSWIRE_BACKEND_URL: backendUrl,
  NEWSWIRE_NEXT_DIST_DIR: distDir,
  NEXT_TELEMETRY_DISABLED: "1",
};

const reusable =
  process.env.E2E_SKIP_BUILD === "1" &&
  fs.existsSync(path.join(standalone, "server.js")) &&
  fs.existsSync(stamp) &&
  fs.readFileSync(stamp, "utf8") === backendUrl;

if (reusable) {
  console.log(`[e2e web] E2E_SKIP_BUILD=1: reusing the existing ${distDir} build`);
} else {
  console.log(`[e2e web] building into ${distDir} (API proxied to ${backendUrl})`);
  // `next build` points next-env.d.ts (and, if needed, tsconfig.json) at the build directory.
  // Put them back afterwards so a test run leaves the working tree as it found it.
  const touched = ["next-env.d.ts", "tsconfig.json"].map((f) => [path.join(frontend, f), fs.readFileSync(path.join(frontend, f))]);
  const build = spawnSync(next, ["build"], { cwd: frontend, env, stdio: "inherit" });
  for (const [file, content] of touched) {
    if (!fs.readFileSync(file).equals(content)) fs.writeFileSync(file, content);
  }
  if (build.status !== 0) {
    console.error("[e2e web] next build failed");
    process.exit(1);
  }
  fs.writeFileSync(stamp, backendUrl);
}

// Same layout as the Dockerfile's run stage.
fs.cpSync(path.join(dist, "static"), path.join(standalone, distDir, "static"), { recursive: true });
fs.cpSync(path.join(frontend, "public"), path.join(standalone, "public"), { recursive: true });

const web = spawn(process.execPath, ["server.js"], {
  cwd: standalone,
  env: { ...env, NODE_ENV: "production", HOSTNAME: "127.0.0.1", PORT: String(port) },
  stdio: "inherit",
});
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => web.kill(sig));
web.on("exit", (code, signal) => process.exit(code ?? (signal ? 0 : 1)));
