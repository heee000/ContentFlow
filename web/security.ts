export const DEFAULT_API_BASE =
  process.env.NEXT_PUBLIC_CONTENTFLOW_API_BASE ||
  "http://localhost:8000/api/v1";

export const RUNTIME_API_BASE_CONFIGURABLE =
  process.env.NODE_ENV !== "production";

function configuredApiUrl(): URL {
  try {
    const url = new URL(DEFAULT_API_BASE);
    if (!["http:", "https:"].includes(url.protocol)) {
      throw new Error("API protocol must be HTTP or HTTPS");
    }
    return url;
  } catch (error) {
    throw new Error(
      "NEXT_PUBLIC_CONTENTFLOW_API_BASE must be an absolute HTTP(S) URL",
      { cause: error },
    );
  }
}

const apiUrl = configuredApiUrl();
const secureDeployment = apiUrl.protocol === "https:";
const connectSources = new Set(["'self'", apiUrl.origin]);
const scriptSources = new Set(["'self'", "'unsafe-inline'"]);

if (RUNTIME_API_BASE_CONFIGURABLE) {
  connectSources.add("http://localhost:*");
  connectSources.add("http://127.0.0.1:*");
  connectSources.add("https:");
  connectSources.add("ws://localhost:*");
  connectSources.add("ws://127.0.0.1:*");
  scriptSources.add("'unsafe-eval'");
}

const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  `connect-src ${[...connectSources].join(" ")}`,
  "font-src 'self' data:",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "img-src 'self' data: blob: https:",
  "media-src 'self' blob: https:",
  "object-src 'none'",
  `script-src ${[...scriptSources].join(" ")}`,
  "style-src 'self' 'unsafe-inline'",
  "worker-src 'self' blob:",
  ...(secureDeployment ? ["upgrade-insecure-requests"] : []),
].join("; ");

export const WEB_SECURITY_HEADERS = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), geolocation=(), microphone=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ...(secureDeployment
    ? [
        {
          key: "Strict-Transport-Security",
          value: "max-age=31536000",
        },
      ]
    : []),
] as const;
