// Starts the FastAPI backend for the browser tests against a throwaway SQLite database.
//
// 1. Wipes and re-creates E2E_DATA_DIR (only if it is empty or was created by this script).
// 2. Runs the Alembic migrations (`python -m app.cli migrate`).
// 3. Runs uvicorn on 127.0.0.1:E2E_API_PORT until Playwright stops it.
//
// NEWSWIRE_ALLOW_PRIVATE_NETWORK=true is set here, for this process only, so the API may fetch
// the fixture feed on 127.0.0.1. Never set it in a real deployment.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const backend = path.resolve(here, "..", "..", "..", "backend");
const python = process.env.NEWSWIRE_E2E_PYTHON ?? path.join(backend, ".venv", "bin", "python");
const port = Number(process.env.E2E_API_PORT ?? 8765);
const webPort = Number(process.env.E2E_WEB_PORT ?? 3765);
const dataDir = path.resolve(process.env.E2E_DATA_DIR ?? path.join(os.tmpdir(), "newswire-e2e"));
const MARKER = ".newswire-e2e";

function fail(message) {
  console.error(`[e2e api] ${message}`);
  process.exit(1);
}

if (!fs.existsSync(python)) {
  fail(`No Python found at ${python}. Create backend/.venv as the README describes, or set NEWSWIRE_E2E_PYTHON.`);
}

// Fresh, throwaway state for every run. Refuse to delete a directory this script did not create.
if (fs.existsSync(dataDir)) {
  const entries = fs.readdirSync(dataDir);
  if (entries.length && !entries.includes(MARKER)) {
    fail(`${dataDir} exists and was not created by the e2e suite. Point E2E_DATA_DIR at an empty or new directory.`);
  }
  fs.rmSync(dataDir, { recursive: true, force: true });
}
fs.mkdirSync(path.join(dataDir, "storage"), { recursive: true });
fs.writeFileSync(path.join(dataDir, MARKER), "Throwaway NEWSWIRE browser-test data. Deleted at the start of every run.\n");

// Only explicit NEWSWIRE_* values reach the API; anything exported in the shell is dropped.
// (Values in a project .env file still apply unless they are set here.)
const inherited = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith("NEWSWIRE_")));
const env = {
  ...inherited,
  NEWSWIRE_ENV: "development",
  NEWSWIRE_DATABASE_URL: `sqlite:///${path.join(dataDir, "newswire.db")}`,
  NEWSWIRE_STORAGE_DIR: path.join(dataDir, "storage"),
  NEWSWIRE_SECRET_KEY: "e2e-only-secret-key-never-use-outside-the-browser-tests-0123456789abcdef",
  NEWSWIRE_PUBLIC_BASE_URL: `http://127.0.0.1:${webPort}`,
  NEWSWIRE_COOKIE_SECURE: "false",
  NEWSWIRE_ALLOW_PRIVATE_NETWORK: "true",
  NEWSWIRE_EMBEDDED_SCHEDULER: "false",
  NEWSWIRE_PER_HOST_DELAY_SECONDS: "0",
  NEWSWIRE_FETCH_TIMEOUT_SECONDS: "10",
  NEWSWIRE_RATE_LIMIT_PER_MINUTE: "100000",
  NEWSWIRE_LOGIN_RATE_LIMIT_PER_MINUTE: "1000",
  NEWSWIRE_BACKUP_ENCRYPTION_KEY: "",
  NEWSWIRE_OPENVERSE_CLIENT_ID: "",
  NEWSWIRE_OPENVERSE_CLIENT_SECRET: "",
  NEWSWIRE_LOG_JSON: "false",
  NEWSWIRE_LOG_LEVEL: process.env.E2E_API_LOG_LEVEL ?? "WARNING",
};

const migrate = spawnSync(python, ["-m", "app.cli", "migrate"], { cwd: backend, env, stdio: "inherit" });
if (migrate.status !== 0) fail("Database migration failed.");

const api = spawn(python, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port), "--no-access-log"], {
  cwd: backend,
  env,
  stdio: "inherit",
});
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => api.kill(sig));
api.on("exit", (code, signal) => process.exit(code ?? (signal ? 0 : 1)));
