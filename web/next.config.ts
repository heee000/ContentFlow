import type { NextConfig } from "next";
import { WEB_SECURITY_HEADERS } from "./security";

const nextConfig: NextConfig = {
  distDir: process.env.CONTENTFLOW_E2E === "1" ? ".next-e2e" : ".next",
  output: "standalone",
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: WEB_SECURITY_HEADERS.map((header) => ({ ...header })),
      },
    ];
  },
};

export default nextConfig;
