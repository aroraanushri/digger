import type { NextConfig } from "next";
const config: NextConfig = {
  async rewrites() {
    return [{source: "/backend/:path*", destination: `${process.env.DIGGER_API_URL || "http://127.0.0.1:8000"}/api/:path*`}];
  },
};
export default config;
