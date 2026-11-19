import path from 'node:path'
import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  // Enable React strict mode
  reactStrictMode: true,
  // Let the dev server accept requests arriving through the public tunnel.
  allowedDevOrigins: (process.env.PUBLIC_HOSTS ?? '*.ngrok-free.dev,*.ngrok-free.app').split(','),
  turbopack: {
    root: path.resolve(__dirname, '..'),
  },

  // Optimize images
  images: {
    unoptimized: true,
  },

  async rewrites() {
    const backendUrl = process.env.AGENT_BACKEND_URL?.replace(/\/$/, '')
    if (!backendUrl) {
      return []
    }

    // One public address serves everything: phones need HTTPS for the
    // microphone, and Agora cloud needs to reach the MCP tool server.
    return [
      { source: '/api/:path*', destination: `${backendUrl}/:path*` },
      { source: '/mcp', destination: `${backendUrl}/mcp` },
    ]
  },
}

export default nextConfig
