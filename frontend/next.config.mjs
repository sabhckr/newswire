/** @type {import('next').NextConfig} */
const backend = (process.env.NEWSWIRE_BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "SAMEORIGIN" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
];

const nextConfig = {
  output: "standalone",
  // The browser test suite builds into its own directory so it never disturbs a running dev server.
  distDir: process.env.NEWSWIRE_NEXT_DIST_DIR || ".next",
  poweredByHeader: false,
  reactStrictMode: true,
  experimental: {
    // API requests are proxied to FastAPI (see rewrites). Next's default 30 s would cut off slow
    // requests such as publishing to a slow WordPress site; match nginx's 300 s instead.
    // Videos are uploaded in 8 MB pieces, which stays under Next's 10 MB proxied-body limit.
    proxyTimeout: 300_000,
  },
  // The browser only ever talks to this origin; API and media requests are proxied to FastAPI.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/media/:path*", destination: `${backend}/media/:path*` },
    ];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
