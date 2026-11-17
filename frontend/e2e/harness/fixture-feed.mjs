// Local RSS fixture for the browser tests: /robots.txt, /feed.xml and a page per story.
// Publication times are relative to when this server started, so the stories always fall
// inside the "last 7 days" preview window and stay identical across fetches in one run.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const feed = JSON.parse(fs.readFileSync(path.join(here, "..", "fixtures", "feed.json"), "utf8"));
const port = Number(process.env.E2E_FEED_PORT ?? 8766);
const base = `http://127.0.0.1:${port}`;
const startedAt = Date.now();

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function rss() {
  const items = feed.stories
    .map((s) => {
      const url = `${base}/stories/${s.slug}`;
      const date = new Date(startedAt - s.hoursAgo * 3_600_000).toUTCString();
      return `<item><title>${esc(s.title)}</title><link>${url}</link><guid isPermaLink="true">${url}</guid><pubDate>${date}</pubDate><description>${esc(s.summary)}</description></item>`;
    })
    .join("\n    ");
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>${esc(feed.title)}</title>
    <link>${base}/</link>
    <description>${esc(feed.description)}</description>
    <language>en</language>
    ${items}
  </channel>
</rss>
`;
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url ?? "/", base);
  const send = (status, type, body) => {
    res.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store" });
    res.end(req.method === "HEAD" ? undefined : body);
  };
  if (url.pathname === "/robots.txt") return send(200, "text/plain; charset=utf-8", "User-agent: *\nAllow: /\n");
  if (url.pathname === "/feed.xml") return send(200, "application/rss+xml; charset=utf-8", rss());
  const story = feed.stories.find((s) => url.pathname === `/stories/${s.slug}`);
  if (story) {
    return send(200, "text/html; charset=utf-8", `<!doctype html><title>${esc(story.title)}</title><h1>${esc(story.title)}</h1><p>${esc(story.summary)}</p>`);
  }
  return send(404, "text/plain; charset=utf-8", "Not found\n");
});

server.listen(port, "127.0.0.1", () => console.log(`fixture feed on ${base}/feed.xml`));
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => server.close(() => process.exit(0)));
