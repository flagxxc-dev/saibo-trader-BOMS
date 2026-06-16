import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Smaller production footprint on ~1GB VPS (run via scripts/web_run.sh)
  output: "standalone",
  poweredByHeader: false,
  compress: true,
};

export default nextConfig;
