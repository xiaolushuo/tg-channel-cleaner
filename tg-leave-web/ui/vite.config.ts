import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// 开发期把 /api 与 /api/ws 代理到本地后端（127.0.0.1:8787）。
// 生产期后端直接托管 dist，无需代理。
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
