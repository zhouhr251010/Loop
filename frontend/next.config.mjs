/** @type {import('next').NextConfig} */
const backendUrl =
  process.env.BACKEND_INTERNAL_API_BASE_URL ?? "http://127.0.0.1:8001";

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
