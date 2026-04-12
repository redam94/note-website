import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
  // Increase proxy timeout for long-running LLM operations
  experimental: {
    proxyTimeout: 300_000, // 5 minutes
  },
};

export default nextConfig;
