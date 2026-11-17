/**
 * Addresses and fixed test data shared by playwright.config.ts and the specs.
 *
 * Every server the suite starts listens on 127.0.0.1 on its own port, so a development
 * server on 3000/8000 can keep running. Override a port with E2E_WEB_PORT, E2E_API_PORT or
 * E2E_FEED_PORT if one is taken.
 */
import os from "node:os";
import path from "node:path";

export const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3765);
export const API_PORT = Number(process.env.E2E_API_PORT ?? 8765);
export const FEED_PORT = Number(process.env.E2E_FEED_PORT ?? 8766);

export const WEB_URL = `http://127.0.0.1:${WEB_PORT}`;
export const API_URL = `http://127.0.0.1:${API_PORT}`;
/** Local fixture feed served by e2e/harness/fixture-feed.mjs. The suite never contacts a real site. */
export const FEED_URL = `http://127.0.0.1:${FEED_PORT}`;

/** Throwaway SQLite database and storage. Wiped and re-created at the start of every run. */
export const DATA_DIR = process.env.E2E_DATA_DIR ?? path.join(os.tmpdir(), "newswire-e2e");

/** Signed-in browser state written by the first-run test and reused by the later projects. */
export const AUTH_FILE = path.resolve(__dirname, ".auth", "admin.json");

export const ADMIN = {
  name: "Ada Editor",
  email: "ada.editor@example.com",
  password: "correct-horse-battery-e2e",
};
