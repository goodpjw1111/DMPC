/** @type {import('next').NextConfig} */

// SINGLE-ORIGIN deployment (recommended): set API_PROXY_TARGET to the FastAPI
// URL and the browser only ever talks to THIS origin; Next proxies /api and
// /auth to the API. That keeps cookies first-party (SameSite=Lax + __Host-
// work), eliminates CORS, and shrinks the CSRF surface. Leave NEXT_PUBLIC_API_BASE
// empty so the SPA uses same-origin relative URLs.
const apiTarget = process.env.API_PROXY_TARGET; // e.g. http://localhost:8000

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    if (!apiTarget) return [];
    return [
      { source: "/api/:path*", destination: `${apiTarget}/api/:path*` },
      { source: "/auth/:path*", destination: `${apiTarget}/auth/:path*` },
      { source: "/healthz", destination: `${apiTarget}/healthz` },
    ];
  },
};
module.exports = nextConfig;
