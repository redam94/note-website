import type { NextConfig } from "next";

const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["192.168.1.196"],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
  experimental: {
    proxyTimeout: 300_000,
    proxyClientMaxBodySize: 15 * 1024 * 1024, // 15 MB — allows 10 MB chunks + multipart overhead
  },
};

export default nextConfig;
