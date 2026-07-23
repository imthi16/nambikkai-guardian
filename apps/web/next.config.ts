import type { NextConfig } from "next";

// Response headers applied to every route. The connect-src origin for the API
// is configurable so deployments can point the browser at their API host; it
// defaults to same-origin. The script/style inline allowances are a documented
// residual risk (see docs/SECURITY.md) pending nonce-based CSP.
const apiOrigin = process.env.NEXT_PUBLIC_API_ORIGIN ?? "";
const connectSrc = ["'self'", apiOrigin].filter(Boolean).join(" ");

const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "img-src 'self' data:",
  "font-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "script-src 'self' 'unsafe-inline'",
  `connect-src ${connectSrc}`,
  "form-action 'self'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  {
    key: "Permissions-Policy",
    value: "geolocation=(), camera=(), microphone=(), browsing-topics=()",
  },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
